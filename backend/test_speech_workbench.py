import tempfile
import unittest
from pathlib import Path
from backend.speech_runtime import SpeechRuntime, SpeechError, validate_request
from backend.test_speech_runtime import fake_engine, wait_for
from backend.speech_assets import AudioAssets


class WorkbenchTests(unittest.TestCase):
    def test_empty_audio_is_invalid_not_oversized(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(SpeechError) as raised:
                AudioAssets(Path(folder)).upload(b'', 'empty.wav')
            self.assertEqual(raised.exception.code,'INVALID_AUDIO')
            self.assertEqual(raised.exception.status,400)

    def test_pre_upgrade_job_preserves_idempotency(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=SpeechRuntime(Path(folder),Path(folder),engine=fake_engine)
            old={'text':'old','steps':10,'cfg':2.0,'seed':42,'requestId':'before-upgrade','id':'tts-'+'a'*32,'state':'completed','createdAt':'2026-11-01T01:00:00Z'}
            runtime.jobs[old['id']]=old
            try:
                runtime.start(); wait_for(lambda:runtime.status()['state']=='ready')
                self.assertEqual(runtime.submit({'text':'old','requestId':'before-upgrade'})['id'],old['id'])
                self.assertEqual(runtime.submit_batch({'items':[{'text':'old','requestId':'before-upgrade'}]})['items'][0]['id'],old['id'])
            finally: runtime.stop()

    def test_conditioning_and_parameters(self):
        clean = validate_request({'text':'你好', 'mode':'clone', 'referenceAudioId':'aud-'+'a'*32, 'cfg':1.7, 'minTokens':3, 'maxTokens':256, 'streaming':True})
        self.assertEqual(clean['mode'], 'clone')
        self.assertTrue(clean['streaming'])
        for bad in [
            {'text':'hi','mode':'clone'},
            {'text':'hi','mode':'continue','promptAudioId':'aud-'+'a'*32},
            {'text':'hi','referenceAudioId':'../../secret'},
            {'text':'hi','minTokens':100,'maxTokens':50},
            {'text':'hi','normalize':'yes'},
        ]:
            with self.assertRaises(SpeechError): validate_request(bad)

    def test_batch_queue_and_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = SpeechRuntime(Path(folder),Path(folder),engine=fake_engine)
            try:
                runtime.start(); wait_for(lambda:runtime.status()['state']=='ready')
                jobs=runtime.submit_batch({'items':[{'text':'first'},{'text':'second'}]})['items']
                wait_for(lambda:all(runtime.job(j['id'])['state']=='completed' for j in jobs))
                retry=runtime.retry(jobs[0]['id'])
                self.assertNotEqual(retry['id'],jobs[0]['id'])
                wait_for(lambda:runtime.job(retry['id'])['state']=='completed')
                runtime.delete(jobs[0]['id'])
                with self.assertRaises(SpeechError): runtime.job(jobs[0]['id'])
                self.assertEqual(runtime.list_jobs(query='second')['total'],1)
            finally: runtime.stop()

    def test_invalid_batch_has_no_partial_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=SpeechRuntime(Path(folder),Path(folder),engine=fake_engine)
            try:
                runtime.start(); wait_for(lambda:runtime.status()['state']=='ready')
                with self.assertRaises(SpeechError): runtime.submit_batch({'items':[{'text':'valid'},{'text':''}]})
                self.assertEqual(runtime.list_jobs()['total'],0)
            finally: runtime.stop()

    def test_stop_cancels_entire_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=SpeechRuntime(Path(folder),Path(folder),engine=fake_engine)
            try:
                runtime.start(); wait_for(lambda:runtime.status()['state']=='ready')
                jobs=runtime.submit_batch({'items':[{'text':'slow'},{'text':'next'}]})['items']
                runtime.stop()
                self.assertTrue(all(runtime.job(j['id'])['state']=='cancelled' for j in jobs))
            finally: runtime.stop()

    def test_cancel_active_restarts_and_preserves_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=SpeechRuntime(Path(folder),Path(folder),engine=fake_engine)
            try:
                runtime.start(); wait_for(lambda:runtime.status()['state']=='ready')
                payload={'items':[{'text':'slow','requestId':'one'},{'text':'next','requestId':'two'}]}
                jobs=runtime.submit_batch(payload)['items']
                self.assertEqual([j['id'] for j in runtime.submit_batch(payload)['items']],[j['id'] for j in jobs])
                runtime.cancel(jobs[0]['id'])
                wait_for(lambda:runtime.job(jobs[1]['id'])['state']=='completed')
                self.assertEqual(runtime.job(jobs[0]['id'])['state'],'cancelled')
                self.assertIsNone(runtime.status()['activeJobId'])
            finally: runtime.stop()

import json
import tempfile
import time
import unittest
from pathlib import Path

from backend.speech_runtime import SpeechRuntime, SpeechError, validate_request


def fake_engine(connection, model_path, threads):
    connection.send({'ready': True, 'sampleRate': 48000})
    while True:
        job = connection.recv()
        if job['text'] == 'slow':
            time.sleep(30)
        import wave
        with wave.open(job['output'], 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000)
            wav.writeframes(b'\x01\x00' * 4800)
        connection.send({'durationSeconds': 0.1, 'sampleRate': 48000})


def wait_for(predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate(): return
        time.sleep(0.02)
    raise AssertionError('State deadline exceeded')


class SpeechRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = SpeechRuntime(Path(self.temp.name), Path(self.temp.name), engine=fake_engine, startup_timeout=5, generation_timeout=2)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.runtime.stop)

    def ready(self):
        self.runtime.start()
        wait_for(lambda: self.runtime.status()['state'] == 'ready')

    def test_validation(self):
        for value in [{'text':' '}, {'text':'x'*301}, {'text':'ok','steps':0}, {'text':'ok','steps':True}, {'text':'ok','cfg':float('nan')}, {'text':'ok','command':'rm'}, {'text':5}]:
            with self.assertRaises(SpeechError): validate_request(value)
        self.assertEqual(validate_request({'text':' hello '})['text'], 'hello')

    def test_idempotent_start_and_real_result_contract(self):
        self.ready(); pid = self.runtime.process.pid
        self.runtime.start(); self.assertEqual(self.runtime.process.pid, pid)
        job = self.runtime.submit({'text':'hello','requestId':'test-123'})
        self.assertEqual(self.runtime.submit({'text':'hello','requestId':'test-123'})['id'], job['id'])
        wait_for(lambda: self.runtime.job(job['id'])['state'] == 'completed')
        self.assertTrue(self.runtime.audio(job['id']).exists())
        with self.assertRaises(SpeechError): self.runtime.audio('../outside')
        with self.assertRaises(SpeechError): self.runtime.submit({'text':'changed','requestId':'test-123'})
        self.runtime.stop(); self.runtime.stop()
        self.assertEqual(self.runtime.status()['state'],'stopped')

    def test_busy_stop_and_restart(self):
        self.ready(); job = self.runtime.submit({'text':'slow'})
        with self.assertRaises(SpeechError): self.runtime.submit({'text':'another'})
        self.runtime.stop()
        self.assertEqual(self.runtime.job(job['id'])['state'],'cancelled')
        self.ready()
        self.assertEqual(self.runtime.status()['state'],'ready')

    def test_timeout_releases_process(self):
        self.ready(); self.runtime.generation_timeout=0.15
        job=self.runtime.submit({'text':'slow'})
        wait_for(lambda:self.runtime.job(job['id'])['state']=='failed')
        self.assertEqual(self.runtime.status()['state'],'failed')
        self.assertFalse(self.runtime.process and self.runtime.process.is_alive())

    def test_restart_after_process_crash_clears_previous_job(self):
        self.ready()
        job = self.runtime.submit({'text':'slow'})
        with self.runtime.lock:
            self.runtime.process.kill()
            self.runtime.process.join(timeout=2)
            self.assertEqual(self.runtime.status()['state'], 'failed')
            self.runtime.start()
        wait_for(lambda: self.runtime.status()['state'] == 'ready')
        self.assertEqual(self.runtime.job(job['id'])['state'], 'failed')
        self.assertIsNone(self.runtime.status()['activeJobId'])
        next_job = self.runtime.submit({'text':'recovered'})
        wait_for(lambda: self.runtime.job(next_job['id'])['state'] == 'completed')

    def test_restart_marks_interrupted_jobs(self):
        path=Path(self.temp.name)/'tts-interrupted.json'
        path.write_text(json.dumps({'id':'tts-interrupted','state':'running','createdAt':'2026-11-01T00:00:00+00:00'}))
        restarted=SpeechRuntime(Path(self.temp.name),Path(self.temp.name),engine=fake_engine)
        self.assertEqual(restarted.job('tts-interrupted')['state'],'failed')
        self.assertEqual(restarted.job('tts-interrupted')['failureCode'],'WORKER_RESTARTED')


if __name__=='__main__':unittest.main()

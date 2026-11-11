import unittest
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.speech_api import SpeechClient, create_speech_router


class SpeechApiTests(unittest.TestCase):
    def setUp(self):
        self.adapter=AsyncMock(spec=SpeechClient)
        app=FastAPI();app.include_router(create_speech_router(self.adapter))
        self.client=TestClient(app)

    def test_bad_input_never_reaches_worker(self):
        for body in [{'text':''},{'text':'ok','command':'anything'},{'text':'ok','steps':21}]:
            response=self.client.post('/api/speech/jobs',json=body)
            self.assertEqual(response.status_code,400)
            self.assertEqual(response.json()['error']['code'],'INVALID_ARGUMENT')
        self.adapter.request.assert_not_called()

    def test_job_is_asynchronous_and_metadata_enveloped(self):
        self.adapter.request.return_value={'id':'tts-'+'a'*32,'state':'running'}
        response=self.client.post('/api/speech/jobs',json={'text':'你好'})
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()['data']['state'],'running')
        self.assertIsNone(response.json()['error'])

    def test_path_and_lifecycle_allowlist(self):
        self.assertEqual(self.client.get('/api/speech/jobs/not-a-job/audio').status_code,404)
        self.assertEqual(self.client.post('/api/speech/service/delete').status_code,400)
        self.adapter.request.assert_not_called()

    def test_unconfigured_service_fails_closed(self):
        app=FastAPI();app.include_router(create_speech_router(SpeechClient('', '')))
        response=TestClient(app).get('/api/speech/service')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['error']['code'],'SPEECH_NOT_CONFIGURED')

    def test_framework_validation_uses_speech_envelope(self):
        for response in [self.client.post('/api/speech/jobs', json=[]), self.client.get('/api/speech/jobs?page=0'), self.client.post('/api/speech/jobs', content='{')]:
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()['error']['code'], 'INVALID_ARGUMENT')
            self.assertIn('requestId', response.json())
        self.adapter.request.assert_not_called()

    def test_workbench_routes_and_openapi(self):
        spec=self.client.get('/api/speech/openapi.json').json()
        self.assertEqual(spec['openapi'],'3.0.3')
        self.assertIn('referenceAudioId',spec['components']['schemas']['GenerateRequest']['properties'])
        self.adapter.request.return_value={'items':[]}
        self.assertEqual(self.client.post('/api/speech/batches',json={'items':[{'text':'hello'}]}).status_code,202)
        self.assertEqual(self.client.post('/api/speech/batches',json={'items':[]}).status_code,400)
        self.assertEqual(self.client.get('/api/speech/jobs?state=invalid').status_code,422)
        self.assertEqual(self.client.get('/api/speech/assets/not-valid/audio').status_code,404)
        self.assertEqual(self.client.get('/api/speech/jobs/'+'tts-'+'a'*32+'/chunks/-1').status_code,400)


if __name__=='__main__':unittest.main()

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.speech_worker import create_app


class SpeechWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.secret = 'test-private-worker-key-' + 'a' * 32
        self.environment = patch.dict(os.environ, {
            'XUNTUI_SPEECH_WORKER_KEY': self.secret,
            'XUNTUI_SPEECH_RESULTS': self.directory.name,
            'XUNTUI_SPEECH_MODEL_PATH': self.directory.name,
            'XUNTUI_SPEECH_AUTOSTART': 'false',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_private_endpoints_require_key(self):
        with TestClient(create_app()) as client:
            self.assertEqual(client.get('/health').status_code, 200)
            self.assertEqual(client.get('/service').status_code, 401)
            self.assertEqual(client.post('/service/stop').status_code, 401)
            self.assertEqual(client.post('/jobs', json={'text':'hello'}).status_code, 401)
            self.assertEqual(client.get('/service', headers={'X-Worker-Key': self.secret}).json()['state'], 'stopped')

    def test_unverified_model_cannot_start(self):
        with TestClient(create_app()) as client:
            response = client.post('/service/start', headers={'X-Worker-Key': self.secret})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['code'], 'MODEL_NOT_VERIFIED')

    def test_weak_key_fails_closed(self):
        with patch.dict(os.environ, {'XUNTUI_SPEECH_WORKER_KEY':'short'}):
            with self.assertRaises(RuntimeError):
                create_app()

    def test_autostart_requires_verified_model(self):
        with patch.dict(os.environ, {'XUNTUI_SPEECH_AUTOSTART':'true'}):
            with self.assertRaisesRegex(RuntimeError, 'verified'):
                with TestClient(create_app()):
                    pass


if __name__ == '__main__':
    unittest.main()

"""Authenticated API coverage without cloud instances or production storage."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import personal_youyun as accounts
from backend.providers.youyun.models import YouyunConfig


class PersonalAccountAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        with patch.dict(os.environ, {'XUNTUI_STORAGE_BACKEND': 'sqlite', 'XUNTUI_DATABASE_PATH': str(root / 'app.db'), 'XUNTUI_RUNTIME_ROOT': str(root / 'runtime'), 'XUNTUI_DATASET_ROOT': str(root / 'datasets'), 'XUNTUI_ARTIFACT_ROOT': str(root / 'artifacts'), 'XUNTUI_SHARED_STORAGE_ENABLED': 'false'}):
            from backend import app
        cls.app = app

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        root = Path(self.root.name)
        self.store = accounts.CredentialStore('sqlite', root / 'settings.db', '', root)
        self.store.initialize()
        self.patches = [patch.object(accounts, 'credential_store', self.store), patch.object(self.app, 'AUTH_MODE', 'required')]
        for item in self.patches:
            item.start()
        self.client = TestClient(self.app.app)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.root.cleanup()

    def user(self, value):
        return {'x-control-user-id': value, 'x-control-username': 'Same display name'}

    def save(self, owner):
        return self.client.put('/api/settings/youyun', headers=self.user(owner), json={
            'publicKey': owner + '-public-key', 'privateKey': owner + '-private-key',
            'region': 'cn-wlcb', 'zone': 'cn-wlcb-01'})

    def test_settings_authentication_isolation_masking_and_validation(self):
        self.assertEqual(self.client.get('/api/settings/youyun').status_code, 401)
        self.assertEqual(self.save('alice').status_code, 200)
        self.assertFalse(self.client.get('/api/settings/youyun', headers=self.user('bob')).json()['configured'])
        self.assertEqual(self.save('bob').status_code, 200)
        result = self.client.get('/api/settings/youyun', headers=self.user('alice')).json()
        self.assertNotIn('alice-private', str(result))
        self.assertNotIn('alice-public', str(result))
        rejected = self.client.put('/api/settings/youyun', headers=self.user('alice'), json={'privateKey': ['sensitive-invalid-value']})
        self.assertEqual(rejected.status_code, 422)
        self.assertNotIn('sensitive-invalid-value', rejected.text)
        for user in ('alice', 'bob'):
            self.assertEqual(self.client.get('/api/datasets', headers=self.user(user)).status_code, 200)

    def test_connection_test_uses_request_account_and_never_creates_instances(self):
        self.save('alice'); self.save('bob')
        seen = []
        def describe(client, *args, **kwargs):
            seen.append(client.config.private_key)
            return [{'region': 'cn-wlcb', 'zone': 'cn-wlcb-01'}]
        with patch('backend.providers.youyun.client.YouyunClient.describe_support_zones', describe):
            for user in ('alice', 'bob'):
                response = self.client.post('/api/settings/youyun/test', headers=self.user(user))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()['connected'])
        self.assertEqual(seen, ['alice-private-key', 'bob-private-key'])

    def test_current_gateway_login_overrides_previous_local_session(self):
        self.save('alice'); self.save('bob')
        self.store.save('alice', {'region': 'cn-sh2', 'zone': 'cn-sh2-02'})
        token = self.app.session_manager.create({'id': 'alice', 'sub': 'alice', 'display_name': 'Alice'})
        self.client.cookies.set(self.app.AUTH_COOKIE_NAME, token)
        response = self.client.get('/api/settings/youyun', headers=self.user('bob'))
        self.assertEqual(response.json()['region'], 'cn-wlcb')
        identity = self.client.get('/api/auth/me', headers=self.user('bob')).json()
        self.assertEqual(identity['sub'], 'bob')

    def test_other_user_cannot_control_resource_or_supply_service_id(self):
        self.save('alice'); self.save('bob')
        with accounts.user_scope('alice'):
            accounts.bind_resource('ft-alice')
            accounts.bind_resource('svc-alice')
        for route in ('/api/tasks/ft-alice/stop', '/api/tasks/ft-alice/retry', '/api/tasks/ft-alice/youyun/destroy', '/api/inference/services/svc-alice/start', '/api/inference/services/svc-alice/chat'):
            self.assertEqual(self.client.post(route, headers=self.user('bob'), json={}).status_code, 403, route)
        self.assertEqual(self.client.delete('/api/tasks/ft-alice', headers=self.user('bob')).status_code, 403)

    def test_provider_factories_restore_original_account_for_background_cleanup(self):
        self.save('alice')
        with accounts.user_scope('alice'):
            accounts.bind_resource('ft-alice')
            accounts.bind_resource('svc-alice')
        self.store.save('alice', {'privateKey': 'rotated-private-key'})
        fake_service = {'id': 'svc-alice', 'config': {'youyunResource': {'region': 'cn-wlcb', 'zone': 'cn-wlcb-01'}}}
        from backend.inference_service import InferenceRuntime
        runtime = InferenceRuntime.__new__(InferenceRuntime)
        runtime.remote_provider_factory = None
        runtime.port = 8011
        class Store:
            def get(self, key):
                return fake_service
        runtime.store = Store()
        provider = runtime._make_remote_provider('svc-alice')
        self.assertEqual(provider.manager.client.config.private_key, 'alice-private-key')
        runner = self.app.build_visual_youyun_runner(object(), {'id': 'ft-alice'})
        self.assertEqual(runner.manager.client.config.private_key, 'alice-private-key')

    def test_worker_drops_creator_context_and_resolves_each_queued_owner(self):
        self.save('alice'); self.save('bob')
        for user in ('alice', 'bob'):
            with accounts.user_scope(user):
                accounts.bind_resource('ft-' + user)
        @accounts.background_worker
        async def worker():
            observed = []
            for user in ('bob', 'alice'):
                with accounts.resource_scope('ft-' + user):
                    observed.append(await asyncio.to_thread(lambda: YouyunConfig.from_env().private_key))
            return observed
        with accounts.user_scope('alice'):
            self.assertEqual(asyncio.run(worker()), ['bob-private-key', 'alice-private-key'])

    def test_cleanup_does_not_reuse_another_users_active_executor(self):
        from backend.image_generation_training_runtime import ImageGenerationTrainingRuntime
        self.save('alice'); self.save('bob')
        seen = []
        class Executor:
            def __init__(self, key):
                self.key = key
            async def cleanup_remote(self, task):
                seen.append((self.key, task['id']))
                return {'cleanupState': 'released'}
        runtime = ImageGenerationTrainingRuntime(task_root=Path(self.root.name) / 'image-tasks', execution_lock=asyncio.Lock(), executor_factory=lambda _: Executor(YouyunConfig.from_env().private_key))
        with accounts.user_scope('bob'):
            runtime.create_task({'id': 'igt-bob', 'provider': 'youyun', 'state': 'failed', 'remoteHandle': {'instanceId': 'bob-instance'}, 'cleanupState': 'failed'})
        runtime._active_task_id = 'igt-alice'
        runtime._active_executor = Executor('alice-private-key')
        with accounts.user_scope('bob'):
            result = asyncio.run(runtime.force_cleanup_remote_task('igt-bob'))
        self.assertEqual(result['cleanupState'], 'released')
        self.assertEqual(seen, [('bob-private-key', 'igt-bob')])


if __name__ == '__main__':
    unittest.main()

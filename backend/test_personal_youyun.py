import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import personal_youyun as accounts
from backend.providers.youyun.models import YouyunConfig


class PersonalYouyunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = accounts.CredentialStore('sqlite', Path(self.temp.name) / 'accounts.db', '', Path(self.temp.name))
        self.store.initialize()
        self.previous = accounts.credential_store
        accounts.credential_store = self.store
        self.env = patch.dict(os.environ, {'YOUYUN_PUBLIC_KEY': 'server-public', 'YOUYUN_PRIVATE_KEY': 'server-private'})
        self.env.start()

    def tearDown(self):
        accounts.credential_store = self.previous
        self.env.stop()
        self.temp.cleanup()

    def save(self, user, secret):
        return self.store.save(user, {'publicKey': user + '-public', 'privateKey': secret, 'region': 'cn-wlcb', 'zone': 'cn-wlcb-01'})

    def test_users_do_not_share_keys_or_fall_back_to_server(self):
        self.save('alice', 'alice-private')
        self.save('bob', 'bob-private')
        with accounts.user_scope('alice'):
            self.assertEqual(YouyunConfig.from_env().private_key, 'alice-private')
        with accounts.user_scope('bob'):
            self.assertEqual(YouyunConfig.from_env().private_key, 'bob-private')
        with accounts.user_scope('new-user'):
            self.assertEqual(YouyunConfig.from_env().private_key, '')

    def test_binding_survives_rotation_and_restart_and_blocks_other_user(self):
        self.save('alice', 'old-secret')
        with accounts.user_scope('alice'):
            accounts.bind_resource('ft-alice', remote=True)
        self.save('alice', 'new-secret')
        accounts.credential_store = accounts.CredentialStore('sqlite', Path(self.temp.name) / 'accounts.db', '', Path(self.temp.name))
        with accounts.resource_scope('ft-alice'):
            self.assertEqual(YouyunConfig.from_env().private_key, 'old-secret')
        with accounts.user_scope('bob'):
            with self.assertRaises(accounts.AccountAccessError):
                with accounts.resource_scope('ft-alice'):
                    pass

    def test_storage_and_status_do_not_expose_secrets(self):
        status = self.save('alice', 'never-plaintext-secret')
        self.assertTrue(status['configured'])
        self.assertNotIn('never-plaintext-secret', str(status))
        self.assertNotIn(b'never-plaintext-secret', (Path(self.temp.name) / 'accounts.db').read_bytes())
        self.store.save('alice', {'publicKey': '', 'privateKey': '', 'region': 'cn-wlcb', 'zone': 'cn-wlcb-01'})
        with accounts.user_scope('alice'):
            self.assertEqual(YouyunConfig.from_env().private_key, 'never-plaintext-secret')

    def test_missing_settings_cannot_bind_cloud_resource(self):
        with accounts.user_scope('new-user'):
            with self.assertRaises(accounts.AccountAccessError):
                accounts.bind_resource('ft-new', remote=True)

    def test_legacy_resource_never_uses_request_users_account(self):
        self.save('alice', 'alice-private')
        with accounts.user_scope('alice'):
            with self.assertRaises(accounts.AccountAccessError):
                with accounts.resource_scope('ft-legacy'):
                    pass
        with accounts.resource_scope('ft-legacy'):
            self.assertEqual(YouyunConfig.from_env().private_key, 'server-private')


if __name__ == '__main__':
    unittest.main()

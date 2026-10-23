# -*- coding: utf-8 -*-
"""The QuickBooks OAuth client secret and tokens are stored encrypted (the listing says so) and only the administrator
reads them. Without MIGRATION_ENCRYPTION_KEY the module used a fixed text that is not a valid Fernet key, the encryption
failed and the value was stored as it came; the OAuth callback wrote the tokens as they came; and every internal user
could read, change and delete the connection."""
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

SECRETS = {
    'oauth_client_secret_encrypted': 'client-secret',
    'oauth_access_token_encrypted': 'access-token',
    'oauth_refresh_token_encrypted': 'refresh-token',
}


@tagged('post_install', '-at_install')
class TestQbSecrets(TransactionCase):

    def setUp(self):
        super().setUp()
        self.connection = self.env['qb.connection'].create({
            'name': 'QB secrets', 'connection_type': 'oauth', 'oauth_client_id': 'client-id'})
        self.connection.write(SECRETS)
        self.env.invalidate_all()

    def _stored(self):
        self.env.flush_all()
        self.env.cr.execute('SELECT %s FROM qb_connection WHERE id = %%s' % ', '.join(SECRETS), [self.connection.id])
        return dict(zip(SECRETS, self.env.cr.fetchone()))

    def test_01_stored_encrypted_and_read_back(self):
        stored = self._stored()
        for field, secret in SECRETS.items():
            with self.subTest(field=field):
                self.assertTrue(stored[field])
                self.assertNotIn(secret, stored[field])
        self.assertEqual(self.connection._get_oauth_client_secret(), 'client-secret')
        self.assertEqual(self.connection._get_oauth_access_token(), 'access-token')
        self.assertEqual(self.connection._get_oauth_refresh_token(), 'refresh-token')

    def test_02_internal_user_neither_reads_nor_changes_them(self):
        internal = new_test_user(self.env, login='qb-secrets-internal', groups='base.group_user')
        connection = self.connection.with_user(internal)
        for field in SECRETS:
            with self.subTest(field=field), self.assertRaises(AccessError):
                connection.read([field])
        with self.assertRaises(AccessError):
            connection.write({'name': 'changed by an internal user'})
        with self.assertRaises(AccessError):
            connection.unlink()

    def test_03_refresh_stores_the_new_tokens_encrypted(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 3600}
        with patch('requests.post', return_value=response) as post:
            self.connection.refresh_access_token()
        self.assertEqual(post.call_args.kwargs['data']['refresh_token'], 'refresh-token')
        stored = self._stored()
        self.assertNotIn('new-access', stored['oauth_access_token_encrypted'])
        self.assertNotIn('new-refresh', stored['oauth_refresh_token_encrypted'])
        self.assertEqual(self.connection._get_oauth_access_token(), 'new-access')
        self.assertEqual(self.connection._get_oauth_refresh_token(), 'new-refresh')

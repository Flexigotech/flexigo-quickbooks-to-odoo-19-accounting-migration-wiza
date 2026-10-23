# -*- coding: utf-8 -*-
"""Connecting QuickBooks Online from Odoo, end to end, with Intuit's token endpoint simulated. Up to x.0.1.0.3 nobody
could: the connection form had no client fields and no button, /qb/oauth/authorize and /qb/oauth/callback ended in
http.redirect (odoo.http has no such function), every error rendered a template that does not exist (and the transaction
was rolled back), the callback read realm_id where Intuit sends realmId, the state was a fixed text, the code exchange
did not send the redirect_uri, and the sandbox pointed to hosts that are not QuickBooks Online (Intuit's sandbox
discovery document: authorization at appcenter.intuit.com; the API at sandbox-quickbooks.api.intuit.com)."""
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from odoo.tests import HttpCase, new_test_user, tagged

from ..lib.qbo_api_client import QboApiClient
from ..lib.qbo_reports_client import QboReportsClient


@tagged('post_install', '-at_install')
class TestQbOauth(HttpCase):

    def setUp(self):
        super().setUp()
        new_test_user(self.env, login='qb-oauth-admin',
                      groups='base.group_system,flexigo_migration_base.group_migration_manager')
        self.connection = self.env['qb.connection'].create({
            'name': 'QB OAuth', 'connection_type': 'oauth', 'oauth_client_id': 'client-id',
            'oauth_client_secret_encrypted': 'client-secret'})
        self.authenticate('qb-oauth-admin', 'qb-oauth-admin')

    def _start(self):
        response = self.url_open('/qb/oauth/authorize?connection_id=%d' % self.connection.id, allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        url = urlparse(response.headers['Location'])
        return url, {k: v[0] for k, v in parse_qs(url.query).items()}

    def test_01_authorize_goes_to_intuit_with_a_fresh_state(self):
        url, query = self._start()
        self.assertEqual('%s://%s%s' % (url.scheme, url.netloc, url.path), 'https://appcenter.intuit.com/connect/oauth2')
        self.assertEqual(query['client_id'], 'client-id')
        self.assertEqual(query['redirect_uri'], self.connection.get_base_url() + '/qb/oauth/callback')
        self.assertGreaterEqual(len(query['state']), 32)
        self.assertNotEqual(query['state'], self._start()[1]['state'])
        self.connection.is_sandbox = True
        self.assertEqual(self._start()[0].netloc, 'appcenter.intuit.com')

    def test_02_callback_stores_the_tokens_and_opens_the_connection(self):
        query = self._start()[1]
        token = MagicMock(status_code=200)
        token.json.return_value = {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 3600,
                                   'x_refresh_token_expires_in': 8726400}
        with patch('requests.post', return_value=token) as post:
            response = self.url_open('/qb/oauth/callback?code=the-code&realmId=9130&state=%s' % query['state'],
                                     allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn('id=%d' % self.connection.id, response.headers['Location'])
        self.assertEqual(post.call_args.kwargs['data'], {
            'grant_type': 'authorization_code', 'code': 'the-code', 'redirect_uri': query['redirect_uri']})
        self.assertEqual(post.call_args.kwargs['auth'], ('client-id', 'client-secret'))
        self.connection.invalidate_recordset()
        self.assertEqual(self.connection.realm_id, '9130')
        self.assertEqual(self.connection._get_oauth_access_token(), 'new-access')
        self.assertEqual(self.connection._get_oauth_refresh_token(), 'new-refresh')

    def test_03_a_callback_odoo_did_not_start_stores_nothing(self):
        self._start()
        with patch('requests.post') as post:
            for url in ('/qb/oauth/callback?code=c&realmId=1&state=forged', '/qb/oauth/callback?code=c&realmId=1',
                        '/qb/oauth/callback?error=access_denied&state=x'):
                with self.subTest(url=url):
                    response = self.url_open(url, allow_redirects=False)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn('QuickBooks', response.text)
        post.assert_not_called()
        self.connection.invalidate_recordset()
        self.assertFalse(self.connection.realm_id)

    def test_04_the_form_connects(self):
        arch = self.env['qb.connection'].get_view(view_type='form')['arch']
        for name in ('oauth_client_id', 'oauth_client_secret_encrypted', 'oauth_redirect_uri', 'realm_id',
                     'action_connect_qbo', 'action_test_connection'):
            with self.subTest(name=name):
                self.assertIn('name="%s"' % name, arch)
        self.assertEqual(self.connection.action_connect_qbo()['url'],
                         '/qb/oauth/authorize?connection_id=%d' % self.connection.id)

    def test_05_sandbox_talks_to_the_quickbooks_online_sandbox(self):
        sandbox = 'https://sandbox-quickbooks.api.intuit.com'
        self.assertEqual(QboApiClient('token', '9130', sandbox=True).base_url, sandbox)
        self.assertEqual(QboReportsClient('token', '9130', sandbox=True).base_url, sandbox)
        self.connection.write({'is_sandbox': True, 'realm_id': '9130', 'oauth_access_token_encrypted': 'token'})
        info = MagicMock(status_code=200)
        info.json.return_value = {'CompanyInfo': {'CompanyName': 'Sandbox Co'}}
        with patch('requests.get', return_value=info) as get:
            self.connection.action_test_connection()
        self.assertTrue(get.call_args.args[0].startswith(sandbox + '/v3/company/9130/'))

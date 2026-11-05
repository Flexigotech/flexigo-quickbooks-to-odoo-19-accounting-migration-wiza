# -*- coding: utf-8 -*-
"""Connecting QuickBooks Online from Odoo, end to end, with Intuit's token endpoint simulated. Up to x.0.1.0.3 nobody
could: the connection form had no client fields and no button, /qb/oauth/authorize and /qb/oauth/callback ended in
http.redirect (odoo.http has no such function), every error rendered a template that does not exist (and the transaction
was rolled back), the callback read realm_id where Intuit sends realmId, the state was a fixed text, the code exchange
did not send the redirect_uri, and the sandbox pointed to hosts that are not QuickBooks Online (Intuit's sandbox
discovery document: authorization at appcenter.intuit.com; the API at sandbox-quickbooks.api.intuit.com)."""
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlencode, urlparse

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tools import mute_logger

from ..lib.qbo_api_client import QboApiClient
from ..lib.qbo_reports_client import QboReportsClient


@tagged('post_install', '-at_install')
class TestQbOauth(HttpCase):

    def setUp(self):
        super().setUp()
        self.admin = new_test_user(self.env, login='qb-oauth-admin',
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
        # each refused return logs a warning on purpose
        with patch('requests.post') as post, mute_logger('odoo.addons.flexigo_quickbooks_to_odoo_19_accounting_migration_wiza'
                                                         '.controllers.qb_oauth_controller'):
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
        menu = self.env.ref('flexigo_quickbooks_to_odoo_19_accounting_migration_wiza.menu_qb_connections')
        self.assertEqual((menu.parent_id, menu.action), (self.env.ref('flexigo_migration_base.menu_migration_root'),
                         self.env.ref('flexigo_quickbooks_to_odoo_19_accounting_migration_wiza.action_qb_connection')))
        arch = self.env['qb.connection'].with_user(self.admin).get_view(view_type='form')['arch']
        for name in ('oauth_client_id', 'oauth_client_secret_encrypted', 'oauth_redirect_uri', 'realm_id',
                     'action_connect_qbo', 'action_test_connection'):
            with self.subTest(name=name):
                self.assertIn('name="%s"' % name, arch)
        self.assertEqual(self.connection.with_user(self.admin).action_connect_qbo()['url'],
                         '/qb/oauth/authorize?connection_id=%d' % self.connection.id)
        with self.assertRaises(UserError):
            self.env['qb.connection'].create({'name': 'No client', 'connection_type': 'oauth'}).action_connect_qbo()

    def test_06_who_cannot_change_the_connection_cannot_connect_it(self):
        new_test_user(self.env, login='qb-oauth-internal', groups='base.group_user')
        self.authenticate('qb-oauth-internal', 'qb-oauth-internal')
        with mute_logger('odoo.http'):  # Odoo logs the refused access as a warning
            response = self.url_open('/qb/oauth/authorize?connection_id=%d' % self.connection.id, allow_redirects=False)
        self.assertEqual(response.status_code, 403)

    def test_07_a_state_serves_once(self):
        query = self._start()[1]
        token = MagicMock(status_code=200)
        token.json.return_value = {'access_token': 'a', 'refresh_token': 'r'}
        url = '/qb/oauth/callback?code=c&realmId=9130&state=%s' % query['state']
        with patch('requests.post', return_value=token) as post, mute_logger(
                'odoo.addons.flexigo_quickbooks_to_odoo_19_accounting_migration_wiza.controllers.qb_oauth_controller'):
            self.assertEqual(self.url_open(url, allow_redirects=False).status_code, 303)
            self.assertEqual(self.url_open(url, allow_redirects=False).status_code, 400)
        self.assertEqual(post.call_count, 1)

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


MGR = 'flexigo_migration_base.group_migration_manager'
CTL = 'odoo.addons.flexigo_quickbooks_to_odoo_19_accounting_migration_wiza.controllers.qb_oauth_controller'


def _token(**extra):
    resp = MagicMock(status_code=200)
    data = {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 3600,
            'x_refresh_token_expires_in': 8726400}
    data.update(extra)
    resp.json.return_value = data
    return resp


@tagged('post_install', '-at_install')
class TestQbOauthGuards(HttpCase):
    """x.0.1.0.5: a connection stays in its company, and every check of the callback is pinned by a test (a review took
    them out one by one and the suite stayed green)."""

    def setUp(self):
        super().setUp()
        new_test_user(self.env, login='qb-guard-admin', groups='base.group_system,%s' % MGR)
        self.manager = new_test_user(self.env, login='qb-guard-manager', groups=MGR)
        self.connection = self.env['qb.connection'].create({
            'name': 'QB Guard', 'connection_type': 'oauth', 'oauth_client_id': 'client-id',
            'oauth_client_secret_encrypted': 'client-secret'})
        self.company_b = self.env['res.company'].create({'name': 'QB Guard Company B'})
        self.manager_b = new_test_user(self.env, login='qb-guard-manager-b', groups=MGR, company_id=self.company_b.id,
                                       company_ids=[(6, 0, [self.company_b.id])])

    def _start(self):
        response = self.url_open('/qb/oauth/authorize?connection_id=%d' % self.connection.id, allow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[:200])
        return parse_qs(urlparse(response.headers['Location']).query)['state'][0]

    def _callback(self, **params):
        return self.url_open('/qb/oauth/callback?' + urlencode(params), allow_redirects=False)

    def test_08_a_manager_of_another_company_does_not_reach_the_connection(self):
        self.assertNotEqual(self.connection.company_id, self.company_b)
        self.assertFalse(self.env['qb.connection'].with_user(self.manager_b).search([('id', '=', self.connection.id)]))
        self.authenticate('qb-guard-manager-b', 'qb-guard-manager-b')
        with mute_logger('odoo.http', CTL):
            start = self.url_open('/qb/oauth/authorize?connection_id=%d' % self.connection.id, allow_redirects=False)
        self.assertEqual(start.status_code, 404)
        # who started in A and is moved to B before Intuit comes back exchanges nothing and writes nothing
        self.authenticate('qb-guard-manager', 'qb-guard-manager')
        state = self._start()
        self.manager.write({'company_ids': [(6, 0, [self.company_b.id])], 'company_id': self.company_b.id})
        with patch('requests.post', return_value=_token()) as post, mute_logger(CTL, 'odoo.http'):
            response = self._callback(code='c', state=state, realmId='777')
        self.assertIn(response.status_code, (400, 403, 404))
        post.assert_not_called()
        self.connection.invalidate_recordset()
        self.assertFalse(self.connection.realm_id)

    def test_09_staging_rows_stay_in_their_company(self):
        project = self.env['migration.project'].create({
            'name': 'QB Guard', 'source_type': 'qbo', 'company_id': self.connection.company_id.id,
            'owner_id': self.env.user.id, 'status': 'draft'})
        row = self.env['qb.staging'].create({'object_type': 'class', 'source_id': 'QB-GUARD-1', 'project_id': project.id})
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(self.env['qb.staging'].with_user(self.manager).search([('id', '=', row.id)]), row)
        self.assertFalse(self.env['qb.staging'].with_user(self.manager_b).search([('id', '=', row.id)]))

    def test_10_odd_realm_ids_are_refused(self):
        self.authenticate('qb-guard-admin', 'qb-guard-admin')
        for realm in ('1/../x?y', '١٢٣', '', '12 3', '²'):
            with self.subTest(realm=realm):
                state = self._start()
                with patch('requests.post', return_value=_token()) as post, mute_logger(CTL):
                    response = self._callback(code='c', state=state, realmId=realm)
                self.assertEqual(response.status_code, 400)
                post.assert_not_called()
        self.connection.invalidate_recordset()
        self.assertFalse(self.connection.realm_id)

    def test_11_an_error_without_the_state_shows_no_foreign_text(self):
        self.authenticate('qb-guard-admin', 'qb-guard-admin')
        self._start()
        with mute_logger(CTL):
            response = self._callback(error='access_denied', error_description='FOREIGN-TEXT-QB')
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('FOREIGN-TEXT-QB', response.text)
        self.assertIn('invalid_state', response.text)

    def test_12_the_callback_checks_the_permission_again(self):
        """Who started as Migration Manager and lost the group before coming back exchanges no code."""
        self.authenticate('qb-guard-manager', 'qb-guard-manager')
        state = self._start()
        field = 'group_ids' if 'group_ids' in self.env['res.users']._fields else 'groups_id'
        self.manager.write({field: [(3, self.env.ref(MGR).id), (4, self.env.ref('base.group_user').id)]})
        self.assertTrue(self.connection.with_user(self.manager).read(['oauth_client_id']))
        with patch('requests.post', return_value=_token()) as post, mute_logger(CTL, 'odoo.http'):
            response = self._callback(code='c', state=state, realmId='1')
        self.assertEqual(response.status_code, 403)
        post.assert_not_called()
        self.connection.invalidate_recordset()
        self.assertFalse(self.connection.realm_id)

    def test_13_an_odd_refresh_expiry_shows_the_error_page(self):
        self.authenticate('qb-guard-admin', 'qb-guard-admin')
        got = {}
        for value in ('abc', 1e30):
            state = self._start()
            with patch('requests.post', return_value=_token(x_refresh_token_expires_in=value)), \
                    mute_logger(CTL, 'odoo.http'):
                got[str(value)] = self._callback(code='c', state=state, realmId='1').status_code
        self.assertEqual(got, {'abc': 400, '1e+30': 400})
        self.connection.invalidate_recordset()
        self.assertFalse(self.connection.realm_id)

    def test_14_the_form_offers_what_the_user_can_do(self):
        no_secret = self.env['qb.connection'].create({'name': 'No secret', 'connection_type': 'oauth',
                                                      'oauth_client_id': 'client-id'})
        with self.assertRaisesRegex(UserError, 'administrator'):
            no_secret.with_user(self.manager).action_connect_qbo()
        arch = etree.fromstring(self.env['qb.connection'].get_view(view_type='form')['arch'])
        self.assertEqual(arch.xpath("//button[@name='action_test_connection']")[0].get('invisible'),
                         "connection_type != 'oauth'")

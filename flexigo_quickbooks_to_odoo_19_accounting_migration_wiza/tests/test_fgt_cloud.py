# -*- coding: utf-8 -*-
"""19.0.1.1.0: QuickBooks through FlexigoTech Cloud (BW plan §2.3-2.6). A new connection goes through FlexigoTech Cloud:
the client secret of the customer's Intuit app and the tokens live in its vault, never in Odoo, and every call to Intuit
is _relay_request on quickbooks@<id> (quickbooks-test@<id> for the sandbox); the OAuth return hands the code to the
relay (_canje) and no token comes back. The connections that existed before 1.1.0 go on exactly as bought ('direct'):
the OAuth with Intuit from this server, Test Connection and their tokens encrypted here. FlexigoTech Cloud is stood in
for on its model class (the HTTP worker of an HttpCase sees it too): nothing here reaches it or Intuit."""
import importlib.util
from unittest.mock import MagicMock, call, patch
from urllib.parse import parse_qs, urlparse

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.modules.neutralize import get_neutralization_queries
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger
from odoo.tools.misc import file_path

from odoo.addons.flexigotech_cloud.exceptions import FlexigotechRelayError, FlexigotechServiceInactive

MODULE = 'flexigo_quickbooks_to_odoo_19_accounting_migration_wiza'
MGR = 'flexigo_migration_base.group_migration_manager'
API = 'https://quickbooks.api.intuit.com'
TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'
TOKENS = ('oauth_client_secret_encrypted', 'oauth_access_token_encrypted', 'oauth_refresh_token_encrypted')


def _response(status=200, data=None):
    response = MagicMock(status_code=status)
    response.json.return_value = data if data is not None else {}
    return response


def _direct(connection):
    """What the 1.1.0 migration leaves on a connection that existed before: 'direct' (SQL, as the migration does)."""
    connection.env.flush_all()
    connection.env.cr.execute("UPDATE qb_connection SET fgt_route = 'direct' WHERE id IN %s", [tuple(connection.ids)])
    connection.invalidate_recordset()
    return connection


def _no_service():
    return FlexigotechServiceInactive('No FlexigoTech Cloud service', 0, 'Settings')


class RelayStandIn:
    """ensure_service and the _relay_* calls of flexigotech.cloud, recorded; the service is on until a test turns it
    off. The relay answers 200 (204 for _canje) and puts the Authorization itself: one from the connector fails."""

    def _stand_in(self):
        cloud_cls = type(self.env['flexigotech.cloud'])
        self.relay_answer = _response(200, {'CompanyInfo': {'CompanyName': 'Cloud Co'}})

        def relay_request(module, platform, method, path, **kwargs):
            self.assertNotIn('authorization', {h.lower() for h in kwargs.get('headers') or {}})
            return _response(204) if platform == '_canje' else self.relay_answer

        self.ensure = self.startPatcher(patch.object(cloud_cls, 'ensure_service', return_value=True))
        self.relay = self.startPatcher(patch.object(cloud_cls, '_relay_request', side_effect=relay_request))
        self.store = self.startPatcher(patch.object(cloud_cls, '_relay_store_credential', return_value=None))
        self.delete = self.startPatcher(patch.object(cloud_cls, '_relay_delete_credential', return_value=None))

    def _new(self, **vals):
        return self.env['qb.connection'].create(dict({'name': 'QB cloud', 'connection_type': 'oauth',
                                                      'oauth_client_id': 'client-id', 'realm_id': '9130'}, **vals))

    def _bought(self):
        """A connection of the version bought before FlexigoTech Cloud: direct, its app and tokens encrypted here."""
        connection = _direct(self._new(name='QB bought'))
        connection.write({'oauth_client_secret_encrypted': 'client-secret',
                          'oauth_access_token_encrypted': 'access-token',
                          'oauth_refresh_token_encrypted': 'refresh-token'})
        return connection

    def _stored(self, connection):
        self.env.flush_all()
        self.env.cr.execute('SELECT %s FROM qb_connection WHERE id = %%s' % ', '.join(TOKENS), [connection.id])
        return self.env.cr.fetchone()


@tagged('post_install', '-at_install')
class TestQbFgtCloud(RelayStandIn, TransactionCase):

    def setUp(self):
        super().setUp()
        self._stand_in()

    def test_01_a_new_connection_goes_through_the_cloud(self):
        connection = self._new(fgt_route='direct')
        self.assertEqual(connection.fgt_route, 'cloud')
        self.assertEqual(connection._fgt_module(), MODULE)
        self.assertEqual(connection._fgt_platform(), 'quickbooks@%d' % connection.id)
        connection.is_sandbox = True
        self.assertEqual(connection._fgt_platform(), 'quickbooks-test@%d' % connection.id)
        self.assertEqual(connection.copy().fgt_route, 'cloud')
        info = self.env['flexigotech.cloud']._cloud_info(MODULE)
        self.assertEqual((info.get('plan'), info.get('estricto'), info.get('plataformas')),
                         ('connector', True, ['quickbooks', 'quickbooks-test']))

    def test_02_the_route_never_changes_after_create(self):
        cloud, bought = self._new(), self._bought()
        for connection, other in ((cloud, 'direct'), (bought, 'cloud')):
            for who in (connection, connection.sudo()):
                with self.subTest(route=connection.fgt_route, sudo=who.env.su), self.assertRaises(UserError):
                    who.write({'fgt_route': other})
        self.assertEqual((cloud.fgt_route, bought.fgt_route), ('cloud', 'direct'))
        cloud.write({'fgt_route': 'cloud', 'name': 'the web client sends the route back'})   # the same route is fine

    def test_03_the_migration_makes_every_existing_connection_direct_and_touches_nothing_else(self):
        bought = self._new(name='QB bought A')
        upload = self.env['qb.connection'].create({'name': 'QB bought B', 'connection_type': 'upload'})
        self.env.flush_all()
        self.env.cr.execute("UPDATE qb_connection SET oauth_client_secret_encrypted = 'enc-secret', "
                            "oauth_access_token_encrypted = 'enc-access' WHERE id = %s", [bought.id])
        ids = (bought.id, upload.id)

        def rows():
            self.env.flush_all()
            self.env.cr.execute('SELECT * FROM qb_connection WHERE id IN %s ORDER BY id', [ids])
            names = [column.name for column in self.env.cr.description]
            return [dict(zip(names, row)) for row in self.env.cr.fetchall()]

        spec = importlib.util.spec_from_file_location(
            'qb_post_migrate_1_1_0', file_path('%s/migrations/19.0.1.1.0/post-migrate.py' % MODULE))
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        before = rows()
        for version in (None, '18.0.1.1.0', '19.0.1.1.0'):   # a new install, a line already split, this one: nothing
            with self.subTest(version=version):
                migration.migrate(self.env.cr, version)
                self.assertEqual(rows(), before)
        migration.migrate(self.env.cr, '19.0.1.0.5')
        after = rows()
        self.assertEqual([row.pop('fgt_route') for row in after], ['direct', 'direct'])
        for row in before:
            row.pop('fgt_route')
        self.assertEqual(after, before)

    def test_04_a_direct_connection_calls_intuit_as_bought(self):
        bought = self._bought()
        info = _response(200, {'CompanyInfo': {'CompanyName': 'Bought Co'}})
        token = _response(200, {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 3600})
        with patch('requests.get', return_value=info) as get, patch('requests.post', return_value=token) as post:
            bought.action_test_connection()
            self.assertTrue(bought.is_validated)
            bought._qbo_api_client().get_company_info()
            bought._qbo_reports_client().get_trial_balance('2026-10-31')
            bought.refresh_access_token()
        urls = [c.args[0] for c in get.call_args_list]
        self.assertEqual(urls, [API + '/v3/company/9130/companyinfo/9130', API + '/v3/company/9130/companyinfo/9130',
                                API + '/v3/company/9130/reports/TrialBalance'])
        self.assertEqual({c.kwargs['headers']['Authorization'] for c in get.call_args_list}, {'Bearer access-token'})
        self.assertEqual(post.call_args.args[0], TOKEN_URL)
        self.assertEqual(post.call_args.kwargs['auth'], ('client-id', 'client-secret'))
        self.assertEqual(post.call_args.kwargs['data'], {'grant_type': 'refresh_token', 'refresh_token': 'refresh-token'})
        self.assertEqual(bought._get_oauth_access_token(), 'new-access')
        self.ensure.assert_not_called()
        self.relay.assert_not_called()

    def test_05_a_cloud_connection_only_calls_the_relay(self):
        connection = self._new()
        platform = 'quickbooks@%d' % connection.id
        with patch('requests.get') as get, patch('requests.post') as post, \
                patch('requests.Session.request') as session:
            connection.action_test_connection()
            self.assertTrue(connection.is_validated)
            api = connection._qbo_api_client()
            api.batch_query(['SELECT * FROM Item'])
            api.get_company_info()
            reports = connection._qbo_reports_client()
            for report in (reports.get_trial_balance, reports.get_aged_receivables, reports.get_aged_payables,
                           reports.get_inventory_valuation):
                report('2026-10-31')
            connection.max_retries = 1                       # the query loop: one call, the error comes back
            self.relay_answer = _response(500)
            with self.assertRaises(Exception), mute_logger('odoo.addons.%s.lib.qbo_api_client' % MODULE):
                connection._qbo_api_client().query('SELECT * FROM Account')
            connection.refresh_access_token()               # FlexigoTech Cloud renews the access itself
        for mock in (get, post, session, self.store):
            mock.assert_not_called()
        self.assertEqual([c.args for c in self.relay.call_args_list], [
            (MODULE, platform, 'GET', 'v3/company/9130/companyinfo/9130'),
            (MODULE, platform, 'POST', 'v3/company/9130/batch'),
            (MODULE, platform, 'GET', 'v3/company/9130/companyinfo/9130'),
            (MODULE, platform, 'GET', 'v3/company/9130/reports/TrialBalance'),
            (MODULE, platform, 'GET', 'v3/company/9130/reports/AgedReceivables'),
            (MODULE, platform, 'GET', 'v3/company/9130/reports/AgedPayables'),
            (MODULE, platform, 'GET', 'v3/company/9130/reports/InventoryValuationSummary'),
            (MODULE, platform, 'GET', 'v3/company/9130/query'),
        ])
        self.assertEqual(self.relay.call_args_list[0].kwargs['params'], {'minorversion': connection.minorversion})
        self.assertEqual(self.relay.call_args_list[-1].kwargs['params']['query'],
                         'SELECT * FROM Account MAXRESULTS 1000 STARTPOSITION 1')
        self.assertTrue(self.ensure.call_args_list)
        self.assertTrue(all(c == call(MODULE, raise_error=True) for c in self.ensure.call_args_list))
        connection.is_sandbox = True
        self.relay_answer = _response(200, {'CompanyInfo': {}})
        connection.action_test_connection()
        self.assertEqual(self.relay.call_args.args[1], 'quickbooks-test@%d' % connection.id)

    def test_06_the_client_secret_goes_to_the_vault_and_never_stays_in_odoo(self):
        connection = self._new()
        with self.assertRaises(UserError):                   # without the connector's consent nothing goes out
            connection.write({'fgt_client_secret': 'the-secret'})
        self.store.assert_not_called()
        connection.write({'fgt_consent': True, 'fgt_client_secret': 'the-secret'})
        self.store.assert_called_once_with(MODULE, 'quickbooks@%d' % connection.id,
                                           {'client_id': 'client-id', 'client_secret': 'the-secret'})
        self.ensure.assert_called_once_with(MODULE, raise_error=True)
        self.assertTrue(connection.fgt_saved_at)
        self.assertFalse(connection.fgt_client_secret)
        self.env.flush_all()
        self.env.cr.execute('SELECT q::text FROM qb_connection q WHERE id = %s', [connection.id])
        self.assertNotIn('the-secret', self.env.cr.fetchone()[0])
        with self.assertRaises(UserError):                   # the field of the bought version takes no secret here
            connection.write({'oauth_client_secret_encrypted': 'other-secret'})
        self.assertEqual(self._stored(connection), (None, None, None))
        sandbox = self._new(is_sandbox=True, fgt_consent=True, fgt_client_secret='sandbox-secret')
        self.assertEqual(self.store.call_args.args[1], 'quickbooks-test@%d' % sandbox.id)
        manager = new_test_user(self.env, login='qb-cloud-manager', groups='base.group_user,' + MGR)
        with self.assertRaises(AccessError):                 # only an administrator sends the secret
            connection.with_user(manager).write({'fgt_client_secret': 'x'})
        with self.assertRaises(UserError):                   # a bought connection keeps its secret here, as before
            self._bought().write({'fgt_client_secret': 'x'})

    def test_07_without_service_saving_connecting_and_testing_call_nothing(self):
        self.ensure.side_effect = _no_service()
        connection = self._new(fgt_consent=True)
        with patch('requests.get') as get, patch('requests.post') as post:
            for action in (lambda: connection.write({'fgt_client_secret': 'the-secret'}),
                           connection.action_connect_qbo, connection.action_test_connection):
                with self.subTest(action=action), self.assertRaises(FlexigotechServiceInactive):
                    action()
        for mock in (get, post, self.store, self.relay):
            mock.assert_not_called()
        self.assertFalse(connection.fgt_saved_at)

    def test_08_a_copy_of_the_database_forgets_the_tokens_and_the_saved_app(self):
        bought, cloud = self._bought(), self._new()
        cloud.sudo().write({'fgt_saved_at': fields.Datetime.now(), 'fgt_connected_on': fields.Datetime.now()})
        queries = list(get_neutralization_queries([MODULE]))
        self.assertTrue(queries)
        for query in queries:
            self.env.cr.execute(query)
        self.env.invalidate_all()
        self.assertEqual(self._stored(bought), (None, None, None))
        self.assertFalse(cloud.fgt_saved_at)
        self.assertEqual(bought.oauth_client_id, 'client-id')
        # code of 1.1.0 on the schema of 1.0.x (a copy neutralized before its -u): the whole neutralization goes on
        self.env.cr.execute('ALTER TABLE qb_connection DROP COLUMN fgt_saved_at, DROP COLUMN fgt_connected_on')
        for query in queries:
            self.env.cr.execute(query)

    def test_09_uninstall_deletes_both_families_and_never_blocks(self):
        from odoo.addons.flexigo_quickbooks_to_odoo_19_accounting_migration_wiza import hooks
        hooks._forget_cloud_credentials(self.env)
        self.assertEqual([c.args for c in self.delete.call_args_list],
                         [(MODULE, 'quickbooks'), (MODULE, 'quickbooks-test')])
        error = FlexigotechRelayError('FlexigoTech Cloud is not responding')
        error.code = 'relay_timeout'
        self.delete.reset_mock()
        self.delete.side_effect = error
        with mute_logger('odoo.addons.%s.hooks' % MODULE):
            hooks._forget_cloud_credentials(self.env)        # logged, not raised; stops at the first failure
        self.assertEqual(self.delete.call_count, 1)
        self.delete.reset_mock()
        with patch.object(type(self.env.cr.postcommit), 'add') as after_commit:
            hooks.uninstall_hook(self.env)                  # after the commit: the relay is not in the transaction
        after_commit.assert_called_once()
        self.delete.assert_not_called()

    def test_10_only_an_administrator_deletes_a_bought_connection(self):
        manager = new_test_user(self.env, login='qb-cloud-unlink', groups='base.group_user,' + MGR)
        bought, cloud = self._bought(), self._new()
        with self.assertRaises(UserError):
            bought.with_user(manager).unlink()
        cloud.with_user(manager).unlink()
        bought.unlink()
        self.assertFalse((bought | cloud).exists())


@tagged('post_install', '-at_install')
class TestQbFgtCloudOauth(RelayStandIn, HttpCase):

    def setUp(self):
        super().setUp()
        self._stand_in()
        self.admin = new_test_user(self.env, login='qb-cloud-admin', groups='base.group_system,' + MGR)
        self.connection = self._new(realm_id=False, fgt_consent=True, fgt_client_secret='the-secret')
        self.authenticate('qb-cloud-admin', 'qb-cloud-admin')

    def _start(self):
        url = self.connection.with_user(self.admin).action_connect_qbo()['url']
        response = self.url_open(url, allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        return {k: v[0] for k, v in parse_qs(urlparse(response.headers['Location']).query).items()}

    def test_01_the_return_hands_the_code_to_the_relay_and_no_token_reaches_odoo(self):
        query = self._start()
        self.assertEqual(query['client_id'], 'client-id')
        with patch('requests.post') as post:
            response = self.url_open('/qb/oauth/callback?code=the-code&realmId=9130&state=%s' % query['state'],
                                     allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        post.assert_not_called()
        self.relay.assert_called_once_with(MODULE, '_canje', 'POST', 'quickbooks@%d' % self.connection.id,
                                           json={'code': 'the-code', 'redirect_uri': query['redirect_uri']},
                                           timeout=30)
        self.connection.invalidate_recordset()
        self.assertEqual(self.connection.realm_id, '9130')
        self.assertTrue(self.connection.fgt_connected_on)
        self.assertEqual(self._stored(self.connection), (None, None, None))

    def test_02_without_service_connect_and_the_return_call_nothing(self):
        query = self._start()
        self.ensure.side_effect = _no_service()
        with self.assertRaises(FlexigotechServiceInactive):
            self.connection.with_user(self.admin).action_connect_qbo()
        with patch('requests.post') as post, mute_logger('odoo.addons.%s.controllers.qb_oauth_controller' % MODULE):
            response = self.url_open('/qb/oauth/callback?code=the-code&realmId=9130&state=%s' % query['state'],
                                     allow_redirects=False)
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()
        self.relay.assert_not_called()
        self.connection.invalidate_recordset()
        self.assertFalse(self.connection.realm_id)
        self.assertFalse(self.connection.fgt_connected_on)

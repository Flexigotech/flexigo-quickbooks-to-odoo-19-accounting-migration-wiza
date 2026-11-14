# -*- coding: utf-8 -*-
"""19.0.1.1.1 (qb100): what the listing and the README promise and 1.1.0 did not do.
- QuickBooks Desktop: the form of a File Upload connection takes the IIF export of the lists, Test Connection says what
  Extract reads from it (or why it cannot be read), and Extract and Load take its accounts, classes, customers,
  vendors and items like those of QuickBooks Online. The IIF is written as the sample files of Intuit's IIF Import Kit:
  a header row per list, columns in any order, a value with a comma in double quotes, transactions after the lists.
- A 429 or 5xx of Intuit is asked again (3 times, Retry-After at most 10 seconds) before the run fails where it was.
- A connection bought before 1.1.0 (direct) renews its access token once with the refresh token on a 401 (Intuit's
  OAuth 2.0 token endpoint, grant_type refresh_token) and asks again; if Intuit does not renew it, it says to connect
  again.
- QuickBooks Migration / Reviewer signs off the reconciliation; other internal users only read it.
- A Migration Manager deletes the staging (with the personal data of customers and vendors) of a loaded project."""
import base64
from unittest.mock import patch

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from .test_fgt_cloud import MGR, MODULE, RelayStandIn, _direct, _response
from .test_qb_extract import QUERY, _inventory_valuation, _trial_balance

REVIEWER = MODULE + '.group_migration_reviewer'
SLEEP = 'odoo.addons.%s.models.qb_connection.clock.sleep' % MODULE
RUN_LOG = 'odoo.addons.flexigo_migration_base.models.migration_run'
IIF = '\r\n'.join([
    '!ACCNT\tNAME\tACCNTTYPE\tDESC\tACCNUM\tEXTRA',
    'ACCNT\tChecking\tBANK\t\tQD1010\t',
    'ACCNT\tConstruction:Materials\tINC\tMaterials\tQD4200\t',
    'ACCNT\tOld Loan\tLTLIAB\t\tQD9999\t',
    '!CLASS\tNAME',
    'CLASS\tEast',
    '!CUST\tNAME\tBADDR1\tBADDR2\tPHONE1\tEMAIL\tHIDDEN',
    'CUST\tJoe Customer\tJoe Customer\t"Anywhere, AZ 85740"\t5554443333\tjoe@example.com\tN',
    'CUST\tOld Customer\t\t\t\t\tY',
    '!VEND\tNAME\tPRINTAS\tPHONE1\tEMAIL\tTAXID',
    'VEND\tJon Vendor\t\t5555555555\t\t',
    '!INVITEM\tNAME\tINVITEMTYPE\tDESC\tACCNT\tCOGSACCNT\tQNTY\tQNTY\tPRICE\tCOST',
    'INVITEM\tWindow\tPART\tWindow\tConstruction:Materials\t\t\t\t"1,250.00"\t40',
    'INVITEM\tSan Domingo\tCOMPTAX\t"CA sales tax, San Domingo"\tChecking\t\t\t\t\t',
    '!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT',
    '!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT',
    '!ENDTRNS',
    'TRNS\t\tBILL\t7/16/1998\tChecking\tJon Vendor\t-59.25',
    'SPL\t\tBILL\t7/16/1998\tConstruction:Materials\tJon Vendor\t59.25',
    'ENDTRNS',
])

PDF = b'%PDF-1.7\x00\x81\x8d binary'


def _b64(text, encoding='cp1252'):
    return base64.b64encode(text.encode(encoding)).decode()   # as the web client sends a file (Odoo 20 too)


@tagged('post_install', '-at_install')
class TestQbDesktop(RelayStandIn, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'QB Desktop Co'})
        cls.project = cls.env['migration.project'].create({'name': 'QB Desktop', 'source_type': 'qbdt',
                                                           'company_id': cls.company.id})
        Account = cls.env['account.account'].with_company(cls.company)
        cls.checking, cls.materials = (Account.create({
            'name': name, 'code': code, 'account_type': kind, 'company_id': cls.company.id})
            for name, code, kind in (('Bank Odoo', 'QD1010', 'asset_cash'), ('Materials Odoo', 'QD4200', 'income')))
        cls.manager = new_test_user(cls.env, login='qb-desktop-manager', groups='base.group_user,' + MGR,
                                    company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])

    def setUp(self):
        super().setUp()
        self._stand_in()
        self.connection = self.env['qb.connection'].create({
            'name': 'QB Desktop', 'connection_type': 'upload', 'company_id': self.company.id,
            'qbdt_file': _b64(IIF), 'qbdt_filename': 'lists.IIF'})

    def _engine(self, run_type):
        run = self.env['migration.run'].create({'project_id': self.project.id, 'run_type': run_type,
                                                'source_ref': 'qb.connection,%d' % self.connection.id})
        getattr(run, 'action_' + run_type)()
        for _i in range(20):
            if run.state in ('done', 'failed'):
                break
            self.env['migration.run']._cron_process()
        return run

    def test_01_the_form_takes_the_desktop_export_and_tests_it(self):
        arch = etree.fromstring(self.env['qb.connection'].with_user(self.manager).get_view(view_type='form')['arch'])
        self.assertTrue(arch.xpath("//field[@name='qbdt_file'][@filename='qbdt_filename']"))
        self.assertFalse(arch.xpath("//button[@name='action_test_connection']")[0].get('invisible'))
        note = self.connection.with_user(self.manager).action_test_connection()
        self.assertEqual(note['params']['type'], 'success', note['params']['message'])
        self.assertEqual(note['params']['message'], 'Extract reads 3 accounts, 1 classes, 2 customers, 1 vendors, '
                                                    '2 items. Not read: SPL (1), TRNS (1).')
        self.assertTrue(self.connection.is_validated)
        self.relay.assert_not_called()

    def test_02_a_file_that_is_no_iif_export_says_why(self):
        for vals, says in (({'qbdt_file': False}, 'Upload the IIF export'),
                           ({'qbdt_file': base64.b64encode(PDF).decode(), 'qbdt_filename': 'x.pdf'},
                            'x.pdf has no account, class, customer, vendor or item list'),
                           ({'qbdt_file': _b64('!TRNS\tTRNSID\nTRNS\t1\n')}, 'has no account')):
            with self.subTest(says=says):
                self.connection.write(vals)
                note = self.connection.with_user(self.manager).action_test_connection()
                self.assertEqual(note['params']['type'], 'danger')
                self.assertIn(says, note['params']['message'])
                self.assertFalse(self.connection.is_validated)
                with self.assertRaisesRegex(UserError, says):
                    self.env['migration.run'].create({
                        'project_id': self.project.id, 'run_type': 'extract',
                        'source_ref': 'qb.connection,%d' % self.connection.id}).action_extract()

    def test_03_extract_and_load_take_the_lists_like_quickbooks_online(self):
        run = self._engine('extract')
        self.assertEqual(run.state, 'done', run.error_messages)
        staged = self.env['migration.staging.record'].search([('project_id', '=', self.project.id)])
        self.assertEqual(sorted(staged.mapped('object_type')), sorted(
            ['account'] * 3 + ['class'] + ['customer'] * 2 + ['vendor'] + ['item'] * 2))
        joe = staged.filtered(lambda s: s.source_id == 'Joe Customer')
        self.assertEqual((joe.normalized_name, joe.raw_payload['PrimaryEmailAddr']['Address']),
                         ('Joe Customer', 'joe@example.com'))
        self.assertFalse(staged.filtered(lambda s: s.source_id == 'Old Customer').normalized_active)
        self.relay.assert_not_called()   # nothing goes out: the file is in Odoo
        # before Load writes anything, the staging is there to look at, from the Migration menu
        menu = self.env.ref(MODULE + '.menu_qb_staging')
        self.assertEqual((menu.parent_id, menu.action.res_model),
                         (self.env.ref('flexigo_migration_base.menu_migration_root'), 'migration.staging.record'))
        self.assertEqual(self.env['migration.staging.record'].with_user(self.manager).search_count(
            [('project_id', '=', self.project.id)]), 9)

        load = self._engine('load')
        self.assertEqual(load.state, 'done', load.error_messages)
        Link = self.env['migration.record.link']

        def loaded(object_type, source_id):
            link = Link.search([('project_id', '=', self.project.id), ('object_type', '=', object_type),
                                ('source_id', '=', source_id)])
            return link and self.env[link.res_model].with_context(active_test=False).browse(link.res_id)
        self.assertEqual(loaded('account', 'Checking'), self.checking)
        self.assertEqual(loaded('account', 'Construction:Materials'), self.materials)
        joe, old, jon = (loaded(t, s) for t, s in (('customer', 'Joe Customer'), ('customer', 'Old Customer'),
                                                   ('vendor', 'Jon Vendor')))
        self.assertEqual((joe.name, joe.email, joe.phone, joe.company_id, joe.customer_rank),
                         ('Joe Customer', 'joe@example.com', '5554443333', self.company, 1))
        self.assertFalse(old.active)
        self.assertEqual((jon.name, jon.supplier_rank), ('Jon Vendor', 1))
        window = loaded('item', 'Window').with_company(self.company)
        self.assertEqual((window.name, window.list_price, window.standard_price, window.property_account_income_id),
                         ('Window', 1250.0, 40.0, self.materials))
        self.assertFalse(loaded('item', 'San Domingo'))   # a sales tax item is no product
        self.assertIn('Old Loan', load.error_messages)    # its number is not in the chart: said, not created
        self.assertIn('East', load.error_messages)        # a class needs its dimension mapping
        self.assertEqual(load.records_failed, 2)


class IntuitPages:
    """QuickBooks Online behind the relay stand-in: empty pages and reports, and the answers queued per entity."""

    def _intuit(self, module, platform, method, path, **kwargs):
        params = kwargs['params']
        if path.endswith('/reports/TrialBalance'):
            return _response(200, _trial_balance(params['end_date']))
        if path.endswith('/reports/InventoryValuationSummary'):
            return _response(200, _inventory_valuation(params['report_date']))
        entity, _where, start, _size = QUERY.fullmatch(params['query']).groups()
        self.calls.append((entity, int(start)))
        if self.queued.get(entity):
            return self.queued[entity].pop(0)
        rows = [{'Id': '1', 'Name': entity, 'DisplayName': entity, 'FullyQualifiedName': entity, 'Active': True}]
        return _response(200, {'QueryResponse': {entity: rows}})


def _throttled(status=429, retry_after=None):
    response = _response(status, {'Fault': {'Error': [{'Message': 'ThrottleExceeded', 'code': '3001'}],
                                            'type': 'SERVICE'}})
    response.headers = {'Retry-After': retry_after} if retry_after else {}
    return response


@tagged('post_install', '-at_install')
class TestQbRetry(IntuitPages, RelayStandIn, TransactionCase):

    def setUp(self):
        super().setUp()
        self._stand_in()
        self.health = self.startPatcher(patch.object(type(self.env['flexigotech.cloud']), '_report_health',
                                                     return_value=True))
        self.relay.side_effect = self._intuit
        self.sleep = self.startPatcher(patch(SLEEP))
        self.calls, self.queued = [], {}
        self.project = self.env['migration.project'].create({'name': 'QB retry', 'source_type': 'qbo'})
        self.run = self.env['migration.run'].create({'project_id': self.project.id, 'run_type': 'extract',
                                                     'source_ref': 'qb.connection,%d' % self._new(fgt_consent=True).id})

    def _cron(self):
        for _i in range(30):
            if self.run.state in ('done', 'failed'):
                break
            self.env['migration.run']._cron_process()
        return self.run.state

    def test_01_a_429_is_asked_again_after_its_retry_after(self):
        self.queued['Customer'] = [_throttled(retry_after='3'), _throttled(503)]
        self.run.action_extract()
        self.assertEqual(self._cron(), 'done', self.run.error_messages)
        self.assertEqual(self.calls.count(('Customer', 1)), 3)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [3.0, 4.0])
        self.assertEqual(self.env['migration.staging.record'].search_count(
            [('project_id', '=', self.project.id), ('object_type', '=', 'customer')]), 1)

    def test_02_a_long_retry_after_waits_10_seconds_at_most(self):
        self.queued['Vendor'] = [_throttled(retry_after='120')]
        self.run.action_extract()
        self.assertEqual(self._cron(), 'done', self.run.error_messages)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [10.0])

    def test_03_when_the_retries_run_out_the_run_fails_where_it_was_and_goes_on(self):
        self.queued['Customer'] = [_throttled(503) for _i in range(4)]
        self.run.action_extract()
        with mute_logger(RUN_LOG):
            self.assertEqual(self._cron(), 'failed')
        self.assertEqual(self.calls.count(('Customer', 1)), 4)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [2.0, 4.0, 8.0])
        self.assertIn('HTTP 503', self.run.error_messages)
        self.assertEqual(self.run.pending_objects[0], 'customer')
        staged = self.env['migration.staging.record'].search([('project_id', '=', self.project.id)])
        self.assertEqual(sorted(set(staged.mapped('object_type'))), ['account', 'class', 'location', 'taxcode', 'term'])
        self.run.action_extract()
        self.assertEqual(self._cron(), 'done', self.run.error_messages)
        self.assertEqual(self.calls.count(('Account', 1)), 1)   # what was staged is not asked again

    def test_04_a_400_is_not_asked_again(self):
        self.queued['Customer'] = [_throttled(400)]
        self.run.action_extract()
        with mute_logger(RUN_LOG):
            self.assertEqual(self._cron(), 'failed')
        self.assertEqual(self.calls.count(('Customer', 1)), 1)
        self.sleep.assert_not_called()


@tagged('post_install', '-at_install')
class TestQbDirectRefresh(TransactionCase):

    def setUp(self):
        super().setUp()
        self.connection = _direct(self.env['qb.connection'].create({
            'name': 'QB bought', 'connection_type': 'oauth', 'oauth_client_id': 'client-id', 'realm_id': '9130'}))
        self.connection.write({'oauth_client_secret_encrypted': 'client-secret',
                               'oauth_access_token_encrypted': 'old-access',
                               'oauth_refresh_token_encrypted': 'old-refresh'})

    def _stored(self):
        self.env.flush_all()
        self.env.cr.execute('SELECT oauth_access_token_encrypted, oauth_refresh_token_encrypted FROM qb_connection '
                            'WHERE id = %s', [self.connection.id])
        return self.env.cr.fetchone()

    def test_01_a_401_renews_the_token_once_and_asks_again(self):
        company = _response(200, {'CompanyInfo': {'CompanyName': 'Bought Co'}})
        token = _response(200, {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 3600,
                                'x_refresh_token_expires_in': 8726400})
        with patch('requests.get', side_effect=[_response(401), company]) as get, \
                patch('requests.post', return_value=token) as post:
            note = self.connection.action_test_connection()
        self.assertEqual(note['params']['type'], 'success', note['params']['message'])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer')
        self.assertEqual(post.call_args.kwargs['data'], {'grant_type': 'refresh_token', 'refresh_token': 'old-refresh'})
        self.assertEqual(post.call_args.kwargs['auth'], ('client-id', 'client-secret'))
        self.assertEqual([c.kwargs['headers']['Authorization'] for c in get.call_args_list],
                         ['Bearer old-access', 'Bearer new-access'])
        access, refresh = self._stored()
        self.assertNotIn('new-access', access)
        self.assertNotIn('new-refresh', refresh)
        self.assertEqual((self.connection._get_oauth_access_token(), self.connection._get_oauth_refresh_token()),
                         ('new-access', 'new-refresh'))
        self.assertTrue(self.connection.oauth_refresh_token_expiry)

    def test_02_when_intuit_does_not_renew_it_says_to_connect_again(self):
        refused = _response(400, {'error': 'invalid_grant'})
        with patch('requests.get', return_value=_response(401)) as get, \
                patch('requests.post', return_value=refused) as post:
            note = self.connection.action_test_connection()
        self.assertEqual((note['params']['type'], get.call_count, post.call_count), ('danger', 1, 1))
        self.assertIn('Connect it again with Connect to QuickBooks Online', note['params']['message'])
        self.assertEqual(self.connection._get_oauth_refresh_token(), 'old-refresh')

    def test_03_a_401_after_a_renewed_token_says_to_connect_again(self):
        token = _response(200, {'access_token': 'new-access', 'refresh_token': 'new-refresh'})
        with patch('requests.get', return_value=_response(401)) as get, \
                patch('requests.post', return_value=token):
            note = self.connection.action_test_connection()
        self.assertEqual((note['params']['type'], get.call_count), ('danger', 2))
        self.assertIn('Connect it again', note['params']['message'])


@tagged('post_install', '-at_install')
class TestQbSignOffAndStaging(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env['migration.project'].create({'name': 'QB sign-off', 'source_type': 'qbo'})
        cls.line = cls.env['migration.reconciliation'].create({
            'project_id': cls.project.id, 'reconciliation_type': 'trial_balance', 'account_code': '1010',
            'source_amount': 100.0, 'target_amount': 100.0})
        cls.reviewer = new_test_user(cls.env, login='qb100-reviewer', groups='base.group_user,' + REVIEWER)
        cls.manager = new_test_user(cls.env, login='qb100-manager', groups='base.group_user,' + MGR)
        cls.internal = new_test_user(cls.env, login='qb100-internal', groups='base.group_user')

    def test_01_the_reviewer_signs_off_the_reconciliation(self):
        arch = etree.fromstring(self.env['migration.reconciliation'].with_user(self.reviewer).get_view(
            view_type='form')['arch'])
        self.assertTrue(arch.xpath("//header/button[@name='action_qb_sign_off']"))
        self.line.with_user(self.reviewer).action_qb_sign_off()
        self.assertEqual((self.line.signed_off, self.line.signed_off_by), (True, self.reviewer))
        self.assertTrue(self.line.signed_off_date)

    def test_02_the_others_do_not_sign_off_and_the_reviewer_changes_nothing_else(self):
        for user in (self.internal, self.manager):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                self.line.with_user(user).action_qb_sign_off()
        with self.assertRaises(AccessError):
            self.line.with_user(self.reviewer).write({'source_amount': 1.0})
        arch = etree.fromstring(self.env['migration.reconciliation'].with_user(self.internal).get_view(
            view_type='form')['arch'])
        self.assertFalse(arch.xpath("//button[@name='action_qb_sign_off']"))
        self.assertFalse(self.line.signed_off)

    def _loaded_project(self):
        for status in ('connected', 'extracted', 'mapped', 'validated', 'loaded'):
            self.project.status = status
        Staging = self.env['migration.staging.record']
        staged = Staging.create([{'project_id': self.project.id, 'object_type': 'customer', 'source_id': str(i),
                                  'normalized_name': 'Customer %d' % i,
                                  'raw_payload': {'PrimaryEmailAddr': {'Address': 'c%d@example.com' % i}}}
                                 for i in (1, 2)])
        link = self.env['migration.record.link'].create({
            'project_id': self.project.id, 'object_type': 'customer', 'source_id': '1', 'res_model': 'res.partner',
            'res_id': self.env['res.partner'].create({'name': 'Customer 1'}).id})
        return staged, link

    def test_03_a_migration_manager_deletes_the_staging_of_a_loaded_project(self):
        staged, link = self._loaded_project()
        arch = etree.fromstring(self.env['migration.project'].with_user(self.manager).get_view(
            view_type='form')['arch'])
        self.assertTrue(arch.xpath("//header/button[@name='action_qb_delete_staging'][@confirm]"))
        self.project.with_user(self.manager).action_qb_delete_staging()
        self.assertFalse(staged.exists())
        self.assertTrue(link.exists())   # loading again creates nothing twice

    def test_04_not_before_it_is_loaded_and_not_by_others(self):
        staged, _link = self._loaded_project()
        with self.assertRaises(AccessError):
            self.project.with_user(self.internal).action_qb_delete_staging()
        run = self.env['migration.run'].create({'project_id': self.project.id, 'run_type': 'extract',
                                                'state': 'paused'})
        with self.assertRaisesRegex(UserError, 'still going on'):
            self.project.with_user(self.manager).action_qb_delete_staging()
        run.state = 'failed'
        draft = self.env['migration.project'].create({'name': 'QB draft', 'source_type': 'qbo'})
        with self.assertRaisesRegex(UserError, 'once it is loaded'):
            draft.with_user(self.manager).action_qb_delete_staging()
        self.assertEqual(len(staged.exists()), 2)

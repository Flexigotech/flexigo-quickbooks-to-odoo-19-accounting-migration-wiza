# -*- coding: utf-8 -*-
"""19.0.1.1.0, BW step 4b-1: QuickBooks Online extracted into the staging by the migration engine of
flexigo_migration_base (migration.run, the real one). Only a connection through FlexigoTech Cloud extracts: each page is
one _relay_request (quickbooks@<id>, quickbooks-test@<id> for the sandbox) to Intuit's query endpoint, STARTPOSITION
from 1 and MAXRESULTS 1000 until a short page, open invoices and bills only (and every credit memo and vendor credit:
the open ones cannot be asked for), and then the Trial Balance to an end_date. A connection bought before FlexigoTech
Cloud ('direct') keeps what it bought and does not extract. A refusal of FlexigoTech Cloud pauses the run where it was.
The relay is stood in for as in test_fgt_cloud: nothing reaches it or Intuit."""
import json
import re
from collections import Counter
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.flexigotech_cloud.exceptions import FlexigotechRelayError, FlexigotechServiceInactive

from .test_fgt_cloud import MODULE, RelayStandIn, _response

QUERY = re.compile(r"SELECT \* FROM (\w+)(?: WHERE (.+))? STARTPOSITION (\d+) MAXRESULTS (\d+)")
TYPES = ['account', 'taxcode', 'term', 'class', 'location', 'customer', 'vendor', 'item', 'invoice', 'creditmemo',
         'bill', 'vendorcredit', 'custom']
QUERIES = [
    "SELECT * FROM Account STARTPOSITION 1 MAXRESULTS 1000",
    "SELECT * FROM TaxCode STARTPOSITION 1 MAXRESULTS 1000",
    "SELECT * FROM Term STARTPOSITION 1 MAXRESULTS 1000",
    "SELECT * FROM Class STARTPOSITION 1 MAXRESULTS 1000",
    "SELECT * FROM Department STARTPOSITION 1 MAXRESULTS 1000",
    "SELECT * FROM Customer STARTPOSITION 1 MAXRESULTS 1000",
    "SELECT * FROM Customer STARTPOSITION 1001 MAXRESULTS 1000",
    "SELECT * FROM Vendor STARTPOSITION 1 MAXRESULTS 1000",
    "SELECT * FROM Item STARTPOSITION 1 MAXRESULTS 1000",
    "SELECT * FROM Invoice WHERE Balance > '0' STARTPOSITION 1 MAXRESULTS 1000",
    # every credit note: RemainingCredit is not filterable and VendorCredit.Balance only in QBW (Finance.xsd)
    "SELECT * FROM CreditMemo STARTPOSITION 1 MAXRESULTS 1000",
    "SELECT * FROM Bill WHERE Balance > '0' STARTPOSITION 1 MAXRESULTS 1000",
    "SELECT * FROM VendorCredit STARTPOSITION 1 MAXRESULTS 1000",
]
COUNTS = {'account': 1, 'taxcode': 1, 'class': 1, 'location': 1, 'customer': 1001, 'vendor': 1, 'item': 1, 'invoice': 1,
          'creditmemo': 1, 'bill': 1, 'vendorcredit': 1, 'custom': 1}


def _trial_balance(end_date):
    """The shape of Intuit's report (Header, Columns, Rows), shortened."""
    return {'Header': {'ReportName': 'TrialBalance', 'EndPeriod': end_date, 'Currency': 'USD'},
            'Columns': {'Column': [{'ColTitle': '', 'ColType': 'Account'}, {'ColTitle': 'Debit', 'ColType': 'Money'},
                                   {'ColTitle': 'Credit', 'ColType': 'Money'}]},
            'Rows': {'Row': [{'ColData': [{'value': 'Checking', 'id': '33'}, {'value': '1201.00'}, {'value': ''}]}]}}


def _refusal(code):
    """What flexigotech.cloud raises when the relay refuses a call (402 no_service, permit_expired, busy…)."""
    error = FlexigotechServiceInactive('Refused by FlexigoTech Cloud', 0, 'Settings') if code == 'no_service' \
        else FlexigotechRelayError('Refused by FlexigoTech Cloud')
    error.code = code
    return error


@tagged('post_install', '-at_install')
class TestQbExtract(RelayStandIn, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env['migration.project'].create({'name': 'QB extract', 'source_type': 'qbo'})

    def setUp(self):
        super().setUp()
        self._stand_in()
        self.health = self.startPatcher(patch.object(type(self.env['flexigotech.cloud']), '_report_health',
                                                     return_value=True))
        self.relay.side_effect = self._intuit
        self.refuse, self.answers = {}, {}
        self.intuit = {   # the QuickBooks Online company: rows of each entity, in Intuit's JSON
            'Account': [{'Id': '33', 'Name': 'Checking', 'FullyQualifiedName': 'Checking', 'AcctNum': '1010',
                         'Active': True}],
            'TaxCode': [{'Id': '2', 'Name': 'TAX', 'Active': True}],
            'Term': [],
            'Class': [{'Id': '5', 'Name': 'East', 'FullyQualifiedName': 'East', 'Active': True}],
            'Department': [{'Id': '1', 'Name': 'Garden', 'FullyQualifiedName': 'Garden', 'Active': True}],
            'Customer': [{'Id': str(i), 'DisplayName': 'Customer %d' % i, 'Active': True} for i in range(1, 1002)],
            'Vendor': [{'Id': '56', 'DisplayName': "Bob's Burger Joint", 'Active': True}],
            'Item': [{'Id': '11', 'Name': 'Pump', 'FullyQualifiedName': 'Pump', 'Sku': 'P-11', 'Active': True}],
            'Invoice': [{'Id': '130', 'DocNumber': '1037', 'CustomerRef': {'value': '1', 'name': 'Customer 1'},
                         'Balance': 362.07}],
            'CreditMemo': [{'Id': '160', 'DocNumber': '1040', 'CustomerRef': {'value': '1', 'name': 'Customer 1'},
                            'RemainingCredit': 50.0}],
            'Bill': [{'Id': '150', 'VendorRef': {'value': '56', 'name': "Bob's Burger Joint"}, 'Balance': 108.09}],
            'VendorCredit': [{'Id': '170', 'VendorRef': {'value': '56', 'name': "Bob's Burger Joint"},
                              'Balance': 8.09}],
        }

    def _intuit(self, module, platform, method, path, **kwargs):
        """Intuit behind the relay: a page of the query (no entity key when there are no rows) or the report."""
        self.assertEqual((module, method), (MODULE, 'GET'))
        self.assertNotIn('authorization', {h.lower() for h in kwargs.get('headers') or {}})
        params = kwargs['params']
        if path == 'v3/company/9130/reports/TrialBalance':
            return _response(200, _trial_balance(params['end_date']))
        self.assertEqual(path, 'v3/company/9130/query')
        entity, _where, start, size = QUERY.fullmatch(params['query']).groups()
        key = (entity, int(start))
        if key in self.refuse:
            raise self.refuse.pop(key)
        if key in self.answers:
            return self.answers.pop(key)
        rows = self.intuit[entity][int(start) - 1:int(start) - 1 + int(size)]
        page = {entity: rows, 'startPosition': int(start), 'maxResults': len(rows)} if rows else {}
        return _response(200, {'QueryResponse': page, 'time': '2026-11-06T04:00:00.000-07:00'})

    def _run(self, connection):
        return self.env['migration.run'].create({'project_id': self.project.id, 'run_type': 'extract',
                                                 'source_ref': '%s,%d' % (connection._name, connection.id)})

    def _cron(self, run, until=('done', 'failed'), passes=30):
        for _i in range(passes):
            if run.state in until:
                break
            self.env['migration.run']._cron_process()
        return run.state

    def _staged(self):
        return self.env['migration.staging.record'].search([('project_id', '=', self.project.id)], order='id')

    def _counts(self):
        return dict(Counter(self._staged().mapped('object_type')))

    def _snapshot(self):
        return [(s.id, s.object_type, s.source_id, json.dumps(s.raw_payload, sort_keys=True)) for s in self._staged()]

    def test_01_object_types_in_load_order_known_to_the_engine(self):
        connection = self._new()
        self.assertEqual(connection._migration_object_types(), TYPES)
        self.assertFalse(set(TYPES) - set(dict(self.env['migration.staging.record']._fields['object_type'].selection)))
        self.assertIn('qb.connection', dict(self.env['migration.run']._selection_source_ref()))

    def test_02_a_cloud_connection_extracts_every_entity_page_by_page_only_through_the_relay(self):
        connection = self._new(fgt_consent=True)
        run = self._run(connection)
        with patch('requests.get') as get, patch('requests.post') as post, \
                patch('requests.Session.request') as session:
            run.action_extract()
            self.assertEqual(self._cron(run), 'done')
        for mock in (get, post, session):
            mock.assert_not_called()
        calls = self.relay.call_args_list
        self.assertEqual({c.args[:3] for c in calls}, {(MODULE, 'quickbooks@%d' % connection.id, 'GET')})
        self.assertEqual([c.kwargs['params']['query'] for c in calls[:-1]], QUERIES)
        self.assertEqual({c.args[3] for c in calls[:-1]}, {'v3/company/9130/query'})
        self.assertEqual({c.kwargs['params']['minorversion'] for c in calls}, {'75'})
        today = fields.Date.to_string(fields.Date.context_today(connection))
        self.assertEqual((calls[-1].args[3], calls[-1].kwargs['params']),
                         ('v3/company/9130/reports/TrialBalance', {'end_date': today, 'minorversion': '75'}))
        self.assertEqual(self._counts(), COUNTS)
        report = self._staged().filtered(lambda s: s.object_type == 'custom')
        self.assertEqual((report.source_id, report.normalized_code, report.raw_payload),
                         ('TrialBalance', today, _trial_balance(today)))
        invoice = self._staged().filtered(lambda s: s.object_type == 'invoice')
        self.assertEqual((invoice.source_id, invoice.source_realm, invoice.raw_payload),
                         ('130', '9130', self.intuit['Invoice'][0]))
        self.assertEqual(run.objects_processed, sum(COUNTS.values()))

    def test_03_a_sandbox_connection_extracts_through_quickbooks_test(self):
        self.intuit['Customer'] = self.intuit['Customer'][:2]
        connection = self._new(fgt_consent=True, is_sandbox=True)
        run = self._run(connection)
        run.action_extract()
        self.assertEqual(self._cron(run), 'done')
        self.assertEqual({c.args[1] for c in self.relay.call_args_list}, {'quickbooks-test@%d' % connection.id})

    def test_04_a_direct_connection_does_not_extract_and_calls_nothing(self):
        bought = self._bought()
        unconnected = self._new(name='QB not connected', fgt_consent=True, realm_id=False)
        with patch('requests.get') as get, patch('requests.post') as post, \
                patch('requests.Session.request') as session:
            for connection, says in ((bought, 'create a new QuickBooks connection'), (unconnected, 'Connect')):
                with self.subTest(connection=connection.name):
                    run = self._run(connection)
                    with self.assertRaises(UserError) as refused:
                        run.action_extract()
                    self.assertIn(says, str(refused.exception))
                    self._cron(run)
                    self.assertFalse(run.state)
                    with self.assertRaises(UserError):   # nor when the engine asks for a page some other way
                        connection._migration_extract_page('account', None)
        self.assertFalse(self._staged())
        for mock in (get, post, session, self.relay, self.ensure):
            mock.assert_not_called()

    def test_05_extracting_again_updates_the_same_staging_rows(self):
        self.intuit['Customer'] = self.intuit['Customer'][:3]
        connection = self._new(fgt_consent=True)
        run = self._run(connection)
        run.action_extract()
        self._cron(run)
        first = self._staged()
        self.intuit['Customer'][0] = dict(self.intuit['Customer'][0], DisplayName='Customer 1 Ltd')
        for again in (self._run(connection), run):   # a new run of the project, and Extract on the finished one
            again.action_extract()
            self.assertEqual(self._cron(again), 'done')
            self.assertEqual(self._staged(), first)
        changed = first.filtered(lambda s: s.object_type == 'customer' and s.source_id == '1')
        self.assertEqual((changed.normalized_name, changed.validation_status), ('Customer 1 Ltd', 'pending'))

    def test_06_a_refusal_of_the_relay_mid_run_pauses_it_with_its_cursor_and_the_staging_as_it_was(self):
        connection = self._new(fgt_consent=True)
        for code in ('no_service', 'permit_expired', 'busy', 'credential_missing'):
            with self.subTest(code=code):
                self.health.reset_mock()
                run = self._run(connection)
                run.action_extract()
                self.refuse[('Customer', 1001)] = _refusal(code)
                before = None
                for _i in range(30):
                    if run.state == 'paused':
                        break
                    before = self._snapshot()
                    self.env['migration.run']._cron_process()
                self.assertEqual(run.state, 'paused')
                self.assertEqual((run.cursor, run.pending_objects[0]), (1001, 'customer'))
                self.assertIn('Refused by FlexigoTech Cloud', run.error_messages)
                self.assertEqual(self._snapshot(), before)
                self.health.assert_called_once_with(MODULE, {}, [{'type': 'relay', 'code': code, 'count': 1}])
                self.assertEqual(self._cron(run), 'done')
                self.assertEqual(self._counts(), COUNTS)

    def test_07_the_normalizer(self):
        connection = self._new()
        cases = {
            'account': ({'Id': '33', 'Name': 'Checking', 'FullyQualifiedName': 'Bank:Checking', 'AcctNum': '1010',
                         'Active': False}, 'Bank:Checking', '1010', False),
            'taxcode': ({'Id': '2', 'Name': 'TAX', 'Active': True}, 'TAX', False, True),
            'term': ({'Id': '3', 'Name': 'Net 30'}, 'Net 30', False, True),
            'class': ({'Id': '5', 'Name': 'East', 'FullyQualifiedName': 'Region:East'}, 'Region:East', False, True),
            'location': ({'Id': '1', 'Name': 'Garden', 'FullyQualifiedName': 'Garden', 'Active': False}, 'Garden',
                         False, False),
            'customer': ({'Id': '7', 'DisplayName': "Amy's Bird Sanctuary", 'FullyQualifiedName': 'Amy:Birds',
                          'Active': True}, "Amy's Bird Sanctuary", False, True),
            'vendor': ({'Id': '56', 'DisplayName': "Bob's Burger Joint", 'AcctNum': 'V-1'}, "Bob's Burger Joint",
                       False, True),
            'item': ({'Id': '11', 'Name': 'Pump', 'FullyQualifiedName': 'Garden:Pump', 'Sku': 'P-11'}, 'Garden:Pump',
                     'P-11', True),
            'invoice': ({'Id': '130', 'DocNumber': '1037', 'CustomerRef': {'value': '7', 'name': "Amy's Bird"},
                         'Balance': 362.07}, "Amy's Bird", '1037', True),
            'bill': ({'Id': '150', 'VendorRef': {'value': '56', 'name': "Bob's Burger Joint"}, 'Balance': 108.09},
                     "Bob's Burger Joint", False, True),
            'creditmemo': ({'Id': '160', 'DocNumber': '1040', 'CustomerRef': {'value': '7', 'name': "Amy's Bird"},
                            'RemainingCredit': 50.0}, "Amy's Bird", '1040', True),
            'vendorcredit': ({'Id': '170', 'DocNumber': 'VC-3', 'Balance': 8.09,
                              'VendorRef': {'value': '56', 'name': "Bob's Burger Joint"}}, "Bob's Burger Joint", 'VC-3',
                             True),
        }
        for object_type, (payload, name, code, active) in cases.items():
            with self.subTest(object_type=object_type):
                self.assertEqual(connection._migration_normalize(object_type, payload), {
                    'source_id': payload['Id'], 'source_realm': '9130', 'normalized_name': name,
                    'normalized_code': code, 'normalized_active': active})
        self.assertEqual(connection._migration_normalize('custom', _trial_balance('2026-10-31')), {
            'source_id': 'TrialBalance', 'source_realm': '9130', 'normalized_name': 'TrialBalance',
            'normalized_code': '2026-10-31', 'normalized_active': True})

    def test_08_an_error_of_intuit_fails_the_run_and_keeps_its_cursor(self):
        connection = self._new(fgt_consent=True)
        fault = {'Fault': {'Error': [{'Message': 'Invalid query', 'code': '4000'}], 'type': 'ValidationFault'}}
        for answer in (_response(400, fault), _response(200, {'QueryResponse': fault})):
            with self.subTest(status=answer.status_code):
                run = self._run(connection)
                run.action_extract()
                self.answers[('Customer', 1001)] = answer
                with mute_logger('odoo.addons.flexigo_migration_base.models.migration_run'):
                    self.assertEqual(self._cron(run), 'failed')
                self.assertEqual((run.cursor, run.pending_objects[0]), (1001, 'customer'))
                self.assertIn('QuickBooks', run.error_messages)
                self.health.assert_not_called()

    def test_09_another_intuit_app_or_the_sandbox_on_a_saved_cloud_connection_needs_a_new_connection(self):
        connection = self._new(fgt_consent=True, fgt_client_secret='the-secret')
        saved_at = connection.fgt_saved_at
        self.assertTrue(saved_at)
        for vals in ({'oauth_client_id': 'other-app'}, {'is_sandbox': True}):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                connection.write(vals)
        self.assertEqual((connection.oauth_client_id, connection.is_sandbox, connection.fgt_saved_at),
                         ('client-id', False, saved_at))
        connection.write({'oauth_client_id': 'client-id', 'is_sandbox': False, 'name': 'the same values are fine'})
        self._new().write({'oauth_client_id': 'other-app', 'is_sandbox': True})      # nothing saved yet
        self._bought().write({'oauth_client_id': 'other-app', 'is_sandbox': True})   # bought: as before
        self.store.assert_called_once()

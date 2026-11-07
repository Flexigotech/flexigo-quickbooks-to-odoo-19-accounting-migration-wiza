# -*- coding: utf-8 -*-
"""19.0.1.1.0, BW step 4b-2b: what makes the migration add up. The Trial Balance of QuickBooks at its end date becomes
ONE posted opening entry (in the journal Odoo's own opening entry takes: the first general journal of the company), one
line per mapped account with its balance; the receivable and payable accounts are not one line but one per open invoice
or bill (partner by its link, DocNumber as label, DueDate as due date, Balance as amount, in its currency), so they can
be matched with payments in Odoo and no invoice is created (its taxes and income are already in the balances). What the
open items do not cover is not hidden: it stays on the account with no partner and the A/R or A/P tie-out shows it.
Then Odoo's balance at the end date against QuickBooks', account by account, in migration.reconciliation: what does not
match (0.01) blocks «reconciled». Staged by the real extraction and loaded by the real engine (migration.run)."""
from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from . import test_qb_load
from .test_fgt_cloud import RelayStandIn, _response
from .test_qb_extract import QUERY

CUTOFF = '2026-10-31'


def _ref(value, name):
    return {'value': value, 'name': name}


def _row(account_id, name, debit='', credit=''):
    """A row of Intuit's Trial Balance report: the account (its Id) and its Debit or Credit, as text."""
    return {'ColData': [{'value': name, 'id': account_id}, {'value': debit}, {'value': credit}]}


@tagged('post_install', '-at_install')
class TestQbOpening(RelayStandIn, TransactionCase):
    # the helpers of the master data tests (the module, not the class: its tests are not run twice)
    _engine = test_qb_load.TestQbLoad._engine
    _load = test_qb_load.TestQbLoad._load
    _loaded = test_qb_load.TestQbLoad._loaded

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.usd, cls.eur = cls.env.ref('base.USD'), cls.env.ref('base.EUR')
        cls.eur.active = True
        cls.company = cls.env['res.company'].create({'name': 'QB opening Co', 'currency_id': cls.usd.id})
        cls.project = cls.env['migration.project'].create({'name': 'QB opening', 'source_type': 'qbo',
                                                           'company_id': cls.company.id})
        cls.journal = cls.env['account.journal'].create({'name': 'Miscellaneous', 'code': 'QBMSC', 'type': 'general',
                                                         'company_id': cls.company.id})
        Account = cls.env['account.account'].with_company(cls.company)
        cls.accounts = {code: Account.create({'name': code, 'code': code, 'account_type': kind,
                                              'company_ids': [Command.set(cls.company.ids)]})
                        for code, kind in (('QB1010', 'asset_cash'), ('QB1200', 'asset_receivable'),
                                           ('QB2000', 'liability_payable'), ('QB3000', 'equity'),
                                           ('QB4000', 'income'), ('QB6000', 'expense'))}

    def setUp(self):
        super().setUp()
        self._stand_in()
        self.relay.side_effect = self._intuit
        self.connection = self._new(fgt_consent=True, company_id=self.company.id)
        accounts = (('33', 'Checking', 'QB1010', 'Bank'), ('84', 'Accounts Receivable (A/R)', 'QB1200',
                                                            'Accounts Receivable'),
                    ('91', 'Accounts Payable (A/P)', 'QB2000', 'Accounts Payable'),
                    ('92', 'Retained Earnings', 'QB3000', 'Equity'), ('79', 'Sales', 'QB4000', 'Income'),
                    ('60', 'Rent', 'QB6000', 'Expense'))
        self.intuit = {
            'Account': [{'Id': i, 'Name': name, 'FullyQualifiedName': name, 'AcctNum': code, 'AccountType': kind,
                         'Active': True} for i, name, code, kind in accounts],
            'TaxCode': [], 'Term': [], 'Class': [], 'Department': [], 'Item': [],
            'Customer': [{'Id': '1', 'DisplayName': "Amy's Bird Sanctuary", 'Active': True},
                         {'Id': '2', 'DisplayName': 'Jardins de Lyon', 'Active': True}],
            'Vendor': [{'Id': '56', 'DisplayName': "Bob's Burger Joint", 'Active': True}],
            'Invoice': [
                {'Id': '130', 'DocNumber': '1037', 'CustomerRef': _ref('1', "Amy's Bird Sanctuary"),
                 'DueDate': '2026-11-30', 'CurrencyRef': _ref('USD', 'United States Dollar'), 'TotalAmt': 362.07,
                 'Balance': 300.0},
                # in euros: Balance in its currency, HomeBalance in the home currency (Finance.xsd, Invoice)
                {'Id': '131', 'DocNumber': '1038', 'CustomerRef': _ref('2', 'Jardins de Lyon'),
                 'DueDate': '2026-12-15', 'CurrencyRef': _ref('EUR', 'Euro'), 'ExchangeRate': 1.2, 'TotalAmt': 100.0,
                 'Balance': 100.0, 'HomeBalance': 120.0},
            ],
            'Bill': [{'Id': '150', 'DocNumber': 'B-77', 'VendorRef': _ref('56', "Bob's Burger Joint"),
                      'DueDate': '2026-11-20', 'Balance': 108.09}],
            'CreditMemo': [], 'VendorCredit': [],   # test_08
        }
        # debit 1000 + 420 + 200 = credit 108.09 + 911.91 + 600 = 1620
        self.trial_balance = [_row('33', 'Checking', '1000.00'), _row('84', 'Accounts Receivable (A/R)', '420.00'),
                              _row('60', 'Rent', '200.00'), _row('91', 'Accounts Payable (A/P)', credit='108.09'),
                              _row('92', 'Retained Earnings', credit='911.91'), _row('79', 'Sales', credit='600.00')]

    def _intuit(self, module, platform, method, path, **kwargs):
        """Intuit behind the relay: one short page per entity, and the report at its end date (Header.EndPeriod)."""
        params = kwargs['params']
        if path.endswith('/reports/TrialBalance'):
            total = {'Summary': {'ColData': [{'value': 'TOTAL'}, {'value': ''}, {'value': ''}]}, 'type': 'Section',
                     'group': 'GrandTotal'}
            return _response(200, {
                'Header': {'ReportName': 'TrialBalance', 'EndPeriod': CUTOFF, 'Currency': 'USD'},
                'Columns': {'Column': [{'ColTitle': '', 'ColType': 'Account'}, {'ColTitle': 'Debit', 'ColType': 'Money'},
                                       {'ColTitle': 'Credit', 'ColType': 'Money'}]},
                'Rows': {'Row': [*self.trial_balance, total]}})
        entity, _where, start, _size = QUERY.fullmatch(params['query']).groups()
        rows = self.intuit[entity] if start == '1' else []
        return _response(200, {'QueryResponse': {entity: rows} if rows else {}})

    def _moves(self):
        return self.env['account.move'].search([('company_id', '=', self.company.id)])

    def _lines(self, code):
        return self._loaded('custom', 'TrialBalance').line_ids.filtered(
            lambda line: line.account_id == self.accounts[code])

    def _tie_out(self, kind, code):
        line = self.project.reconciliation_line_ids.filtered(
            lambda r: r.reconciliation_type == kind and r.account_code == code)
        return line.source_amount, line.target_amount, line.is_blocking

    def _to_loaded(self):
        for status in ('connected', 'extracted', 'mapped', 'validated', 'loaded'):
            self.project.status = status

    def test_01_one_posted_opening_entry_with_receivables_and_payables_by_open_item(self):
        load = self._load()
        move = self._loaded('custom', 'TrialBalance')
        self.assertEqual(self._moves(), move, load.error_messages)
        self.assertEqual((move.move_type, move.state, move.date, move.journal_id, move.company_id, move.currency_id),
                         ('entry', 'posted', fields.Date.to_date(CUTOFF), self.journal, self.company, self.usd))
        self.assertEqual({code: sum(self._lines(code).mapped('balance')) for code in self.accounts},
                         {'QB1010': 1000.0, 'QB1200': 420.0, 'QB2000': -108.09, 'QB3000': -911.91, 'QB4000': -600.0,
                          'QB6000': 200.0})
        self.assertEqual(len(self._lines('QB1010')), 1)
        amy, lyon, bob = self._loaded('customer', '1'), self._loaded('customer', '2'), self._loaded('vendor', '56')
        self.assertEqual(sorted((line.name, line.partner_id.id, str(line.date_maturity), line.balance)
                                for line in self._lines('QB1200')),
                         [('1037', amy.id, '2026-11-30', 300.0), ('1038', lyon.id, '2026-12-15', 120.0)])
        self.assertEqual([(line.name, line.partner_id, str(line.date_maturity), line.balance)
                          for line in self._lines('QB2000')], [('B-77', bob, '2026-11-20', -108.09)])
        self.assertEqual(load.records_failed, 0, load.error_messages)   # no invoice or bill is created
        for staging in self.env['migration.staging.record'].search([('project_id', '=', self.project.id),
                                                                    ('object_type', 'in', ('invoice', 'bill'))]):
            self.assertIsNone(self.connection._migration_load_vals(staging))

    def test_02_an_open_item_in_another_currency_keeps_it(self):
        self._load()
        euros = self._lines('QB1200').filtered(lambda line: line.name == '1038')
        self.assertEqual((euros.currency_id, euros.amount_currency, euros.balance), (self.eur, 100.0, 120.0))
        dollars = self._lines('QB1200').filtered(lambda line: line.name == '1037')
        self.assertEqual((dollars.currency_id, dollars.amount_currency, dollars.balance), (self.usd, 300.0, 300.0))

    def test_03_a_trial_balance_that_does_not_balance_creates_nothing(self):
        self.trial_balance[4] = _row('92', 'Retained Earnings', credit='900.00')
        load = self._load()
        self.assertFalse(self._moves())
        self.assertFalse(self._loaded('custom', 'TrialBalance'))
        self.assertIn('custom TrialBalance: ', load.error_messages)
        self.assertIn('does not balance', load.error_messages)

    def test_04_loading_again_does_not_duplicate(self):
        self._load()
        move, tie_outs = self._loaded('custom', 'TrialBalance'), self.project.reconciliation_line_ids
        self._load()
        self.assertEqual(self._moves(), move)
        self.assertEqual(self.project.reconciliation_line_ids, tie_outs)
        self.assertEqual(self.env['migration.record.link'].search_count([('project_id', '=', self.project.id),
                                                                        ('object_type', '=', 'custom')]), 1)

    def test_05_odoo_matches_quickbooks_account_by_account_so_it_can_be_reconciled(self):
        self._load()
        trial_balance = self.project.reconciliation_line_ids.filtered(
            lambda r: r.reconciliation_type == 'trial_balance')
        self.assertEqual(sorted(trial_balance.mapped('account_code')), sorted(self.accounts))
        self.assertEqual(self._tie_out('trial_balance', 'QB1010'), (1000.0, 1000.0, False))
        self.assertEqual(self._tie_out('ar_aging', 'QB1200'), (420.0, 420.0, False))
        self.assertEqual(self._tie_out('ap_aging', 'QB2000'), (-108.09, -108.09, False))
        self.assertEqual(set(self.project.reconciliation_line_ids.mapped('tolerance')), {0.01})
        self.assertFalse(self.project.reconciliation_line_ids.filtered('is_blocking'))
        self._to_loaded()
        self.project.status = 'reconciled'
        self.assertEqual(self.project.status, 'reconciled')

    def test_06_receivables_the_open_items_do_not_cover_are_a_difference_that_blocks_reconciled(self):
        self.trial_balance[1] = _row('84', 'Accounts Receivable (A/R)', '500.00')
        self.trial_balance[4] = _row('92', 'Retained Earnings', credit='991.91')
        load = self._load()
        # the entry adds up: the 80.00 no open item covers stays on the account, with no partner, and is shown
        self.assertEqual([(line.partner_id, line.balance) for line in self._lines('QB1200') if not line.partner_id],
                         [(self.env['res.partner'], 80.0)], load.error_messages)
        self.assertEqual(self._tie_out('trial_balance', 'QB1200'), (500.0, 500.0, False))
        self.assertEqual(self._tie_out('ar_aging', 'QB1200'), (500.0, 420.0, True))
        self._to_loaded()
        with self.assertRaises(UserError):
            self.project.status = 'reconciled'

    def test_07_what_odoo_has_besides_the_opening_shows_in_the_tie_out(self):
        self._load()
        entry = self.env['account.move'].with_company(self.company).create({
            'journal_id': self.journal.id, 'date': CUTOFF, 'line_ids': [
                Command.create({'account_id': self.accounts['QB6000'].id, 'balance': 50.0}),
                Command.create({'account_id': self.accounts['QB1010'].id, 'balance': -50.0})]})
        entry.action_post()
        self._load()
        self.assertEqual(self._tie_out('trial_balance', 'QB1010'), (1000.0, 950.0, True))
        self.assertEqual(self._tie_out('trial_balance', 'QB6000'), (200.0, 250.0, True))
        self._to_loaded()
        with self.assertRaises(UserError):
            self.project.status = 'reconciled'

    def test_08_unapplied_credit_notes_are_open_items_with_the_opposite_sign(self):
        """What is left of a credit memo (RemainingCredit) is a credit on the receivables and of a vendor credit
        (Balance) a debit on the payables (Finance.xsd). In another currency they are at their ExchangeRate: neither has
        a home amount of what is left. One fully applied gives no line."""
        self.intuit['CreditMemo'] = [
            {'Id': '160', 'DocNumber': 'CM-9', 'CustomerRef': _ref('1', "Amy's Bird Sanctuary"),
             'CurrencyRef': _ref('USD', 'United States Dollar'), 'TotalAmt': 80.0, 'Balance': 50.0,
             'RemainingCredit': 50.0},
            {'Id': '161', 'DocNumber': 'CM-10', 'CustomerRef': _ref('2', 'Jardins de Lyon'),
             'CurrencyRef': _ref('EUR', 'Euro'), 'ExchangeRate': 1.2, 'TotalAmt': 10.0, 'RemainingCredit': 10.0}]
        self.intuit['VendorCredit'] = [
            {'Id': '170', 'DocNumber': 'VC-3', 'VendorRef': _ref('56', "Bob's Burger Joint"), 'TotalAmt': 8.09,
             'Balance': 8.09},
            {'Id': '171', 'DocNumber': 'VC-4', 'VendorRef': _ref('56', "Bob's Burger Joint"), 'TotalAmt': 5.0,
             'Balance': 0}]
        # A/R 420 - 50 - 12 = 358, A/P 108.09 - 8.09 = 100: debit 1000 + 358 + 200 = credit 100 + 858 + 600 = 1558
        self.trial_balance[1] = _row('84', 'Accounts Receivable (A/R)', '358.00')
        self.trial_balance[3] = _row('91', 'Accounts Payable (A/P)', credit='100.00')
        self.trial_balance[4] = _row('92', 'Retained Earnings', credit='858.00')
        load = self._load()
        move = self._loaded('custom', 'TrialBalance')
        self.assertEqual(move.state, 'posted', load.error_messages)
        self.assertEqual(self.usd.compare_amounts(sum(move.line_ids.mapped('debit')),
                                                  sum(move.line_ids.mapped('credit'))), 0)
        amy, lyon, bob = self._loaded('customer', '1'), self._loaded('customer', '2'), self._loaded('vendor', '56')
        self.assertEqual(sorted((line.name, line.partner_id.id, line.currency_id.name, line.amount_currency,
                                 line.balance) for line in self._lines('QB1200')),
                         [('1037', amy.id, 'USD', 300.0, 300.0), ('1038', lyon.id, 'EUR', 100.0, 120.0),
                          ('CM-10', lyon.id, 'EUR', -10.0, -12.0), ('CM-9', amy.id, 'USD', -50.0, -50.0)])
        self.assertEqual(sorted((line.name, line.partner_id.id, line.balance) for line in self._lines('QB2000')),
                         [('B-77', bob.id, -108.09), ('VC-3', bob.id, 8.09)])
        # the open items cover the receivables and payables of QuickBooks: nothing is left without a partner
        self.assertEqual(self._tie_out('ar_aging', 'QB1200'), (358.0, 358.0, False))
        self.assertEqual(self._tie_out('ap_aging', 'QB2000'), (-100.0, -100.0, False))
        self.assertFalse(self.project.reconciliation_line_ids.filtered('is_blocking'))
        self._to_loaded()
        self.project.status = 'reconciled'
        self.assertEqual(self.project.status, 'reconciled')
        staged = self.env['migration.staging.record'].search([
            ('project_id', '=', self.project.id), ('object_type', 'in', ('creditmemo', 'vendorcredit'))])
        self.assertEqual(sorted(staged.mapped('source_id')), ['160', '161', '170', '171'])
        for staging in staged:
            self.assertIsNone(self.connection._migration_load_vals(staging))   # no credit note is created

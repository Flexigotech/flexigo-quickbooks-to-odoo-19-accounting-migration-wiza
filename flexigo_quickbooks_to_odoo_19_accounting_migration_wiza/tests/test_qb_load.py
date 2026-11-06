# -*- coding: utf-8 -*-
"""19.0.1.1.0, BW step 4b-2a: the QuickBooks Online master data loaded into Odoo from the staging by the migration
engine of flexigo_migration_base (migration.run.action_load, the real one), after a real extraction through the relay
stand-in, so the staging is what the extractor and its normalizer leave. Accounts are mapped to the chart of the
project's company (the mapping set of the project, else the account number) and only created when the user gave the
code of a new one; customers and vendors become partners, items products, terms payment terms, and classes and
departments analytic accounts in the plan of the dimension mapping. A tax code never becomes a tax, and the open
invoices, the bills and the Trial Balance wait for step 4b-2b. Everything goes to the company of the project, and what
is inactive in QuickBooks is loaded archived. Intuit's JSON as in its schema: github.com/intuit/QuickBooks-V3-Java-SDK
(ipp-v3-java-data/src/main/xsd, Finance.xsd, IntuitNamesTypes.xsd, IntuitBaseTypes.xsd)."""
from odoo import Command
from odoo.tests import TransactionCase, tagged

from .test_fgt_cloud import RelayStandIn, _response
from .test_qb_extract import QUERY, _trial_balance


def _ref(value, name):
    return {'value': value, 'name': name}


@tagged('post_install', '-at_install')
class TestQbLoad(RelayStandIn, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'QB load Co'})
        cls.project = cls.env['migration.project'].create({'name': 'QB load', 'source_type': 'qbo',
                                                           'company_id': cls.company.id})
        cls.mapping = cls.env['migration.mapping.set'].create({'name': 'QB load', 'version': '4b-2a',
                                                               'project_id': cls.project.id})
        cls.project.mapping_set_id = cls.mapping
        Account = cls.env['account.account'].with_company(cls.company)
        cls.checking = Account.create({'name': 'Bank Odoo', 'code': 'QB1010', 'account_type': 'asset_cash',
                                       'company_ids': [Command.set(cls.company.ids)]})
        cls.income = Account.create({'name': 'Product sales Odoo', 'code': 'QB4000', 'account_type': 'income',
                                     'company_ids': [Command.set(cls.company.ids)]})
        cls.plan_class = cls.env['account.analytic.plan'].create({'name': 'QB classes'})
        cls.plan_location = cls.env['account.analytic.plan'].create({'name': 'QB departments'})
        cls.south = cls.env['account.analytic.account'].create({'name': 'South Odoo', 'plan_id': cls.plan_class.id,
                                                                'company_id': cls.company.id})
        Value = cls.env['migration.mapping.value']
        for source, target in (('Sales of Product Income', {'target_record_id': cls.income.id}),
                               ('80', {'target_code': 'QB5000'}),         # a new account: the user gave its code
                               ('Old Loans', {'target_code': 'QB2900'})):
            Value.create(dict(target, mapping_set_id=cls.mapping.id, category='account_type', source_value=source,
                              target_model='account.account'))
        Dimension = cls.env['migration.dimension.map']
        for source, vals in (('East', {'analytic_plan_id': cls.plan_class.id}),
                             ('South', {'analytic_account_id': cls.south.id}),
                             ('West', {'do_not_migrate': True})):
            Dimension.create(dict(vals, mapping_set_id=cls.mapping.id, object_type='class', source_value=source))
        cls.env['qb.dimension.default.map'].create({'dimension_type': 'location', 'source_value': 'Garden',
                                                    'analytic_plan_id': cls.plan_location.id})

    def setUp(self):
        super().setUp()
        self._stand_in()
        self.relay.side_effect = self._intuit
        self.connection = self._new(fgt_consent=True, company_id=self.company.id)
        self.intuit = {
            'Account': [
                {'Id': '33', 'Name': 'Checking', 'FullyQualifiedName': 'Checking', 'AcctNum': 'QB1010',
                 'AccountType': 'Bank', 'AccountSubType': 'Checking', 'Classification': 'Asset', 'Active': True},
                {'Id': '79', 'Name': 'Sales of Product Income', 'FullyQualifiedName': 'Sales of Product Income',
                 'AccountType': 'Income', 'AccountSubType': 'SalesOfProductIncome', 'Classification': 'Revenue',
                 'Active': True},
                {'Id': '80', 'Name': 'Cost of Goods Sold', 'FullyQualifiedName': 'Cost of Goods Sold',
                 'AccountType': 'Cost of Goods Sold', 'AccountSubType': 'SuppliesMaterialsCogs',
                 'Classification': 'Expense', 'Active': True},
                {'Id': '81', 'Name': 'Old Loans', 'FullyQualifiedName': 'Old Loans',
                 'AccountType': 'Long Term Liability', 'AccountSubType': 'NotesPayable',
                 'Classification': 'Liability', 'Active': False},
                {'Id': '90', 'Name': 'Unmapped', 'FullyQualifiedName': 'Unmapped', 'AcctNum': 'NOPE9',
                 'AccountType': 'Expense', 'AccountSubType': 'OfficeGeneralAdministrativeExpenses',
                 'Classification': 'Expense', 'Active': True},
            ],
            'TaxCode': [{'Id': '2', 'Name': 'TAX', 'Active': True, 'Taxable': True}],
            'Term': [
                {'Id': '3', 'Name': 'Net 30', 'DueDays': 30, 'Active': True},
                {'Id': '4', 'Name': '2% 10 Net 30', 'DueDays': 30, 'DiscountPercent': 2, 'DiscountDays': 10,
                 'Active': True},
                {'Id': '5', 'Name': 'Net 60 (old)', 'DueDays': 60, 'Active': False},
                {'Id': '6', 'Name': 'On the 15th', 'DayOfMonthDue': 15, 'DueNextMonthDays': 5,
                 'DiscountDayOfMonth': 10, 'Active': True},
            ],
            'Class': [{'Id': str(i), 'Name': name, 'FullyQualifiedName': name, 'Active': True}
                      for i, name in ((5, 'East'), (6, 'West'), (7, 'North'), (8, 'South'))],
            'Department': [{'Id': '1', 'Name': 'Garden', 'FullyQualifiedName': 'Garden', 'Active': False}],
            'Customer': [{'Id': '1', 'DisplayName': "Amy's Bird Sanctuary", 'CompanyName': "Amy's Bird Sanctuary",
                          'PrimaryEmailAddr': {'Address': 'Birds@Intuit.com'},
                          'PrimaryPhone': {'FreeFormNumber': '(650) 555-3311'},
                          'BillAddr': {'Id': '2', 'Line1': '4581 Finch St.', 'Line2': 'Unit 2', 'City': 'Bayshore',
                                       'CountrySubDivisionCode': 'CA', 'PostalCode': '94326',
                                       'Country': 'United States'},
                          'PrimaryTaxIdentifier': '12-3456789', 'Active': True}],
            'Vendor': [{'Id': '56', 'DisplayName': "Bob's Burger Joint",
                        'PrimaryEmailAddr': {'Address': 'bob@burgers.example'},
                        'TaxIdentifier': 'XXXXXX6789', 'Active': False}],
            'Item': [
                {'Id': '11', 'Name': 'Pump', 'FullyQualifiedName': 'Pump', 'Sku': 'P-11', 'Type': 'Inventory',
                 'UnitPrice': 15, 'PurchaseCost': 10, 'TrackQtyOnHand': True, 'QtyOnHand': 25,
                 'IncomeAccountRef': _ref('79', 'Sales of Product Income'),
                 'ExpenseAccountRef': _ref('80', 'Cost of Goods Sold'), 'Active': True},
                {'Id': '12', 'Name': 'Hose', 'FullyQualifiedName': 'Hose', 'Type': 'NonInventory', 'UnitPrice': 5,
                 'IncomeAccountRef': _ref('90', 'Unmapped'), 'Active': True},
                {'Id': '13', 'Name': 'Design', 'FullyQualifiedName': 'Design', 'Type': 'Service', 'UnitPrice': 75,
                 'Active': False},
                {'Id': '14', 'Name': 'Gardening', 'FullyQualifiedName': 'Gardening', 'Type': 'Category',
                 'Active': True},
            ],
            'Invoice': [{'Id': '130', 'DocNumber': '1037', 'CustomerRef': _ref('1', "Amy's Bird Sanctuary"),
                         'Balance': 362.07}],
            'Bill': [{'Id': '150', 'VendorRef': _ref('56', "Bob's Burger Joint"), 'Balance': 108.09}],
        }

    def _intuit(self, module, platform, method, path, **kwargs):
        """Intuit behind the relay, as in test_qb_extract: one short page per entity, and the report."""
        params = kwargs['params']
        if path == 'v3/company/9130/reports/TrialBalance':
            return _response(200, _trial_balance(params['end_date']))
        entity, _where, start, _size = QUERY.fullmatch(params['query']).groups()
        rows = self.intuit[entity] if start == '1' else []
        return _response(200, {'QueryResponse': {entity: rows} if rows else {}})

    def _engine(self, run_type):
        """A run of the engine on the connection, through its button and the cron, to the end."""
        run = self.env['migration.run'].create({'project_id': self.project.id, 'run_type': run_type,
                                                'source_ref': 'qb.connection,%d' % self.connection.id})
        getattr(run, 'action_' + run_type)()
        for _i in range(40):
            if run.state in ('done', 'failed'):
                break
            self.env['migration.run']._cron_process()
        self.assertEqual(run.state, 'done', run.error_messages)
        return run

    def _load(self):
        self._engine('extract')
        return self._engine('load')

    def _loaded(self, object_type, source_id):
        link = self.env['migration.record.link'].search([('project_id', '=', self.project.id),
                                                         ('object_type', '=', object_type),
                                                         ('source_id', '=', source_id)])
        return link and self.env[link.res_model].with_company(self.company).with_context(
            active_test=False).browse(link.res_id).exists()

    def _links(self):
        return self.env['migration.record.link'].search_count([('project_id', '=', self.project.id)])

    def test_01_accounts_are_mapped_and_only_the_ones_marked_new_are_created(self):
        Account = self.env['account.account'].with_context(active_test=False)
        before = Account.search_count([])
        load = self._load()
        self.assertEqual(self._loaded('account', '33'), self.checking)   # by its number
        self.assertEqual(self._loaded('account', '79'), self.income)     # by the mapping of the user
        self.assertEqual((self.checking.name, self.income.name), ('Bank Odoo', 'Product sales Odoo'))
        cogs, loans = self._loaded('account', '80'), self._loaded('account', '81')
        self.assertEqual((cogs.code, cogs.name, cogs.account_type, cogs.company_ids, cogs.active),
                         ('QB5000', 'Cost of Goods Sold', 'expense_direct_cost', self.company, True))
        self.assertEqual((loans.code, loans.account_type, loans.active), ('QB2900', 'liability_non_current', False))
        self.assertFalse(self._loaded('account', '90'))
        self.assertEqual(Account.search_count([]), before + 2)
        self.assertIn('account 90:', load.error_messages)

    def test_02_loading_again_updates_the_same_records(self):
        self._load()
        links = self._links()
        partner, term = self._loaded('customer', '1'), self._loaded('term', '3')
        self.intuit['Customer'][0]['DisplayName'] = "Amy's Bird Sanctuary Ltd"
        self.intuit['Term'][0]['DueDays'] = 45
        self._load()
        self.assertEqual(self._links(), links)
        self.assertEqual(self._loaded('customer', '1'), partner)
        self.assertEqual(partner.name, "Amy's Bird Sanctuary Ltd")
        self.assertEqual(self.env['res.partner'].search_count([('name', 'like', "Amy's Bird")]), 1)
        self.assertEqual(self._loaded('term', '3'), term)
        self.assertEqual(term.line_ids.mapped('nb_days'), [45])

    def test_03_what_was_deleted_in_odoo_is_not_created_again(self):
        self._load()
        self._loaded('customer', '1').unlink()
        self._loaded('term', '3').unlink()
        load = self._load()
        self.assertFalse(self._loaded('customer', '1'))
        self.assertFalse(self.env['res.partner'].search([('name', '=', "Amy's Bird Sanctuary")]))
        self.assertFalse(self.env['account.payment.term'].search([('name', '=', 'Net 30')]))
        self.assertIn('customer 1: deleted in Odoo', load.error_messages)
        self.assertIn('term 3: deleted in Odoo', load.error_messages)

    def test_04_a_tax_code_never_becomes_a_tax_and_invoices_and_bills_go_into_the_opening(self):
        Tax = self.env['account.tax'].with_context(active_test=False)
        before = Tax.search_count([])
        self._load()
        self.assertEqual(Tax.search_count([]), before)
        staged = self.env['migration.staging.record'].search([('project_id', '=', self.project.id),
                                                              ('object_type', 'in', ('taxcode', 'invoice', 'bill',
                                                                                     'custom'))])
        self.assertEqual(sorted(staged.mapped('object_type')), ['bill', 'custom', 'invoice', 'taxcode'])
        for staging in staged.filtered(lambda s: s.object_type != 'custom'):   # the Trial Balance: test_qb_opening
            self.assertIsNone(self.connection._migration_load_vals(staging), staging.object_type)
            self.assertFalse(self._loaded(staging.object_type, staging.source_id))

    def test_05_customers_and_vendors_become_partners_of_the_company(self):
        self._load()
        amy, bob = self._loaded('customer', '1'), self._loaded('vendor', '56')
        california = self.env['res.country.state'].search([('code', '=', 'CA'),
                                                           ('country_id.code', '=', 'US')])
        self.assertEqual(
            (amy.name, amy.email, amy.phone, amy.street, amy.street2, amy.city, amy.zip, amy.state_id,
             amy.country_id.code, amy.vat, amy.customer_rank, amy.supplier_rank, amy.company_id, amy.active),
            ("Amy's Bird Sanctuary", 'Birds@Intuit.com', '(650) 555-3311', '4581 Finch St.', 'Unit 2', 'Bayshore',
             '94326', california, 'US', '12-3456789', 1, 0, self.company, True))
        # a vendor is another QuickBooks record (DisplayName is unique across customers and vendors): its own partner;
        # a tax id Intuit sends masked is not a tax id
        self.assertEqual((bob.name, bob.email, bob.vat, bob.customer_rank, bob.supplier_rank, bob.company_id,
                          bob.active), ("Bob's Burger Joint", 'bob@burgers.example', False, 0, 1, self.company, False))

    def test_06_items_become_products_with_the_accounts_already_loaded(self):
        self._load()
        pump, hose, design = (self._loaded('item', i) for i in ('11', '12', '13'))
        self.assertEqual((pump._name, pump.name, pump.type, pump.is_storable, pump.default_code, pump.list_price,
                          pump.standard_price, pump.company_id, pump.active),
                         ('product.template', 'Pump', 'consu', True, 'P-11', 15.0, 10.0, self.company, True))
        self.assertEqual((pump.property_account_income_id, pump.property_account_expense_id),
                         (self.income, self._loaded('account', '80')))
        self.assertEqual((hose.type, hose.is_storable, hose.list_price), ('consu', False, 5.0))
        plain = self.env['product.template'].with_company(self.company).create({'name': 'Plain'})
        self.assertEqual(hose.property_account_income_id, plain.property_account_income_id)   # account 90 not loaded
        self.assertEqual((design.type, design.list_price, design.active), ('service', 75.0, False))
        self.assertFalse(self._loaded('item', '14'))   # a category is no product

    def test_07_terms_with_due_days_become_payment_terms_and_date_driven_ones_are_said(self):
        load = self._load()
        net30, discount, old = (self._loaded('term', i) for i in ('3', '4', '5'))
        self.assertEqual([(line.value, line.value_amount, line.delay_type, line.nb_days) for line in net30.line_ids],
                         [('percent', 100.0, 'days_after', 30)])
        self.assertEqual((net30.name, net30.company_id, net30.early_discount, net30.active),
                         ('Net 30', self.company, False, True))
        self.assertEqual((discount.early_discount, discount.discount_percentage, discount.discount_days,
                          discount.line_ids.nb_days), (True, 2.0, 10, 30))
        self.assertEqual((old.line_ids.nb_days, old.active), (60, False))
        # a day of the month with a cut-off (DayOfMonthDue, DueNextMonthDays): no Odoo line says the same
        self.assertFalse(self._loaded('term', '6'))
        self.assertIn('term 6:', load.error_messages)

    def test_08_classes_and_departments_go_to_the_analytic_plan_of_the_dimension_mapping(self):
        load = self._load()
        east, garden = self._loaded('class', '5'), self._loaded('location', '1')
        self.assertEqual((east._name, east.name, east.plan_id, east.company_id, east.active),
                         ('account.analytic.account', 'East', self.plan_class, self.company, True))
        self.assertEqual((garden.name, garden.plan_id, garden.active), ('Garden', self.plan_location, False))
        self.assertEqual(self._loaded('class', '8'), self.south)
        self.assertEqual(self.south.name, 'South Odoo')
        self.assertFalse(self._loaded('class', '6'))   # do not migrate
        self.assertFalse(self._loaded('class', '7'))   # no mapping: said, not guessed
        self.assertIn('class 7:', load.error_messages)
        self.assertNotIn('class 6', load.error_messages)
        # account 90, term 6 and class 7; and the Trial Balance of these tests (a debit and no credit, no receivable
        # account for the open invoice) makes no opening entry (test_qb_opening)
        self.assertEqual(load.records_failed, 4)
        self.assertIn('custom TrialBalance: ', load.error_messages)

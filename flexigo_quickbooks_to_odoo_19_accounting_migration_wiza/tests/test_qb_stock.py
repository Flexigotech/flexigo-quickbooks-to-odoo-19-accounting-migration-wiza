# -*- coding: utf-8 -*-
"""19.0.1.1.0, BW step 4b-2d: the stock at the cut-off. QuickBooks Online's Inventory Valuation Summary at the date of
the Trial Balance (its report_date: QuickBooks-V3-DotNET-SDK, Test/Intuit.Ipp.Test/SDKV3Test/Services/QBO/Reports.cs)
gives the quantity and the asset value of each inventory item at that date (Item.QtyOnHand is today's). They go into
the main warehouse of the project's company, on the products the items became, valued at QuickBooks' value, and with
NO stock valuation entry: the opening entry already brings the inventory account, so the stock must not post it again,
neither in perpetual (real_time) nor in periodic valuation, nor at Odoo's stock closing. Then Odoo's value against
QuickBooks', item by item, in migration.reconciliation ('inventory'). Staged by the real extraction and loaded by the
real engine (migration.run)."""
from datetime import datetime

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from . import test_qb_load, test_qb_opening
from .test_fgt_cloud import RelayStandIn
from .test_qb_opening import _row


def _item(item_id, name, sku, qty, value):
    """A row of the Inventory Valuation Summary: the item (its Id), SKU, Qty, Asset Value and Calc. Avg, as text."""
    return {'ColData': [{'value': name, 'id': item_id}, {'value': sku}, {'value': qty}, {'value': value},
                        {'value': '%.2f' % (float(value) / float(qty))}], 'type': 'Data'}


@tagged('post_install', '-at_install')
class TestQbStock(RelayStandIn, TransactionCase):
    # the helpers of the master data and opening tests (the modules, not the classes: their tests are not run twice)
    _engine = test_qb_load.TestQbLoad._engine
    _load = test_qb_load.TestQbLoad._load
    _loaded = test_qb_load.TestQbLoad._loaded
    _intuit = test_qb_opening.TestQbOpening._intuit
    _moves = test_qb_opening.TestQbOpening._moves
    _tie_out = test_qb_opening.TestQbOpening._tie_out
    _to_loaded = test_qb_opening.TestQbOpening._to_loaded

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.usd = cls.env.ref('base.USD')
        cls.company = cls.env['res.company'].create({'name': 'QB stock Co', 'currency_id': cls.usd.id})
        cls.company.partner_id.tz = 'Europe/Madrid'   # UTC+1 on 31-10-2026
        cls.project = cls.env['migration.project'].create({'name': 'QB stock', 'source_type': 'qbo',
                                                           'company_id': cls.company.id})
        cls.env['account.journal'].create({'name': 'Miscellaneous', 'code': 'QBMSC', 'type': 'general',
                                           'company_id': cls.company.id})
        Account = cls.env['account.account'].with_company(cls.company)
        cls.accounts = {code: Account.create({'name': code, 'code': code, 'account_type': kind,
                                              'company_ids': [Command.set(cls.company.ids)]})
                        for code, kind in (('QB1010', 'asset_cash'), ('QB1300', 'asset_current'),
                                           ('QB3000', 'equity'), ('QB5000', 'expense_direct_cost'))}
        # the products are valued on QuickBooks' Inventory Asset, and a difference at Odoo's stock closing would go to
        # the cost of goods sold (stock_account res_company.py, _get_stock_valuation_account_vals)
        cls.company.account_stock_valuation_id = cls.accounts['QB1300']
        cls.accounts['QB1300'].account_stock_variation_id = cls.accounts['QB5000']
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)

    def setUp(self):
        super().setUp()
        self._stand_in()
        self.relay.side_effect = self._intuit
        self.connection = self._new(fgt_consent=True, company_id=self.company.id)
        accounts = (('33', 'Checking', 'QB1010', 'Bank'), ('81', 'Inventory Asset', 'QB1300', 'Other Current Asset'),
                    ('92', 'Retained Earnings', 'QB3000', 'Equity'),
                    ('80', 'Cost of Goods Sold', 'QB5000', 'Cost of Goods Sold'))
        self.intuit = {
            'Account': [{'Id': i, 'Name': name, 'FullyQualifiedName': name, 'AcctNum': code, 'AccountType': kind,
                         'Active': True} for i, name, code, kind in accounts],
            'TaxCode': [], 'Term': [], 'Class': [], 'Department': [], 'Customer': [], 'Vendor': [],
            'Item': [
                # PurchaseCost is what a purchase starts from, not what the stock is worth; QtyOnHand is today's
                {'Id': '11', 'Name': 'Pump', 'FullyQualifiedName': 'Pump', 'Sku': 'P-11', 'Type': 'Inventory',
                 'PurchaseCost': 9, 'TrackQtyOnHand': True, 'QtyOnHand': 30, 'Active': True},
                {'Id': '12', 'Name': 'Rock', 'FullyQualifiedName': 'Garden:Rock', 'Sku': 'R-12', 'Type': 'Inventory',
                 'PurchaseCost': 2, 'TrackQtyOnHand': True, 'QtyOnHand': 41, 'Active': True},
                {'Id': '13', 'Name': 'Hose', 'FullyQualifiedName': 'Hose', 'Type': 'NonInventory', 'Active': True},
            ],
            'Invoice': [], 'CreditMemo': [], 'Bill': [], 'VendorCredit': [],
        }
        # at the cut-off, 25 pumps worth 250.00 and, in a section, 40 rocks worth 80.00: the Inventory Asset, 330.00
        self.inventory = [
            _item('11', 'Pump', 'P-11', '25', '250.00'),
            {'Header': {'ColData': [{'value': 'Garden'}]}, 'type': 'Section',
             'Rows': {'Row': [_item('12', 'Garden:Rock', 'R-12', '40', '80.00')]},
             'Summary': {'ColData': [{'value': 'Total Garden'}, {'value': ''}, {'value': '40'}, {'value': '80.00'},
                                     {'value': ''}]}},
        ]
        self.trial_balance = [_row('33', 'Checking', '1000.00'), _row('81', 'Inventory Asset', '330.00'),
                              _row('92', 'Retained Earnings', credit='1330.00')]

    def _valuation(self, mode):
        self.company.inventory_valuation = mode
        self.company._set_category_defaults()

    def _product(self, item_id):
        return self._loaded('item', item_id).product_variant_id.with_company(self.company)

    def _stock(self, item_id):
        """Quantity in the main warehouse and Odoo's value of the product the item became."""
        product = self._product(item_id)
        return product.with_context(location=self.warehouse.lot_stock_id.id).qty_available, product.total_value

    def _stock_moves(self):
        return self.env['stock.move'].search([('company_id', '=', self.company.id)])

    def _inventory_account(self):
        return sum(self.env['account.move.line'].search([
            ('company_id', '=', self.company.id), ('parent_state', '=', 'posted'),
            ('account_id', '=', self.accounts['QB1300'].id)]).mapped('balance'))

    def _closing(self):
        """What Odoo's stock closing would post on the inventory account (res_company.py, action_close_stock_valuation)."""
        return [vals for vals in self.company.with_context(
            allowed_company_ids=self.company.ids)._action_close_stock_valuation()
            if vals['account_id'] == self.accounts['QB1300'].id]

    def _check_loaded(self, mode):
        self._valuation(mode)
        load = self._load()
        self.assertEqual((self._product('11').valuation, self._product('12').valuation), (mode, mode))
        self.assertEqual(self._stock('11'), (25.0, 250.0), load.error_messages)
        self.assertEqual(self._stock('12'), (40.0, 80.0))
        moves = self._stock_moves()
        self.assertEqual(set(moves.mapped('state')), {'done'})
        self.assertEqual(set(moves.mapped('location_dest_id')), {self.warehouse.lot_stock_id})
        # at the end of the cut-off day, in UTC and in the company's time zone (23:59:59 in Madrid)
        self.assertEqual(set(moves.mapped('date')), {datetime(2026, 10, 31, 22, 59, 59)})
        # no stock valuation entry: the opening is the only entry, and the inventory account keeps QuickBooks' balance
        self.assertEqual(self._moves(), self._loaded('custom', 'TrialBalance'))
        self.assertEqual(self._inventory_account(), 330.0)
        self.assertEqual(self._closing(), [])
        self.assertEqual(self._tie_out('inventory', 'P-11'), (250.0, 250.0, False))
        self.assertEqual(self._tie_out('inventory', 'R-12'), (80.0, 80.0, False))
        self.assertFalse(self.project.reconciliation_line_ids.filtered('is_blocking'))
        self._to_loaded()
        self.project.status = 'reconciled'
        self.assertEqual(self.project.status, 'reconciled')

    def test_01_perpetual_valuation_loads_the_stock_at_the_cut_off_with_no_valuation_entry(self):
        self._check_loaded('real_time')

    def test_02_periodic_valuation_loads_the_stock_at_the_cut_off_and_the_closing_has_nothing_to_post(self):
        self._check_loaded('periodic')

    def test_03_loading_again_does_not_duplicate(self):
        self._valuation('real_time')
        self._load()
        moves = self._stock_moves()
        self.assertTrue(moves)
        self._load()
        self.assertEqual(self._stock_moves(), moves)
        self.assertEqual(self._stock('11'), (25.0, 250.0))
        self.assertEqual(self._stock('12'), (40.0, 80.0))
        # loading the items again does not put back their purchase cost: it would revalue the stock
        self.assertEqual(self._product('11').standard_price, 10.0)
        self.assertEqual(self._moves(), self._loaded('custom', 'TrialBalance'))
        self.assertEqual(self._inventory_account(), 330.0)
        self.assertEqual(self.env['migration.record.link'].search_count([
            ('project_id', '=', self.project.id), ('object_type', '=', 'custom')]), 2)

    def test_04_an_item_not_loaded_is_said_and_no_stock_is_loaded(self):
        self.inventory.append(_item('99', 'Ghost', 'G-99', '5', '50.00'))
        self.trial_balance[1] = _row('81', 'Inventory Asset', '380.00')
        self.trial_balance[2] = _row('92', 'Retained Earnings', credit='1380.00')
        load = self._load()
        self.assertIn('custom InventoryValuationSummary: ', load.error_messages)
        self.assertIn('Ghost', load.error_messages)
        self.assertFalse(self._stock_moves())
        self.assertEqual(self._stock('11'), (0.0, 0.0))
        self.assertFalse(self._loaded('custom', 'InventoryValuationSummary'))
        self.assertFalse(self.project.reconciliation_line_ids.filtered(lambda r: r.reconciliation_type == 'inventory'))

    def test_05_a_value_that_does_not_match_blocks_reconciled(self):
        self._load()
        self.assertEqual(self._tie_out('inventory', 'P-11'), (250.0, 250.0, False))
        # Odoo revalues the pumps after the load: 25 × 11 = 275 against QuickBooks' 250
        self._product('11').standard_price = 11.0
        self._load()
        self.assertEqual(self._tie_out('inventory', 'P-11'), (250.0, 275.0, True))
        self._to_loaded()
        with self.assertRaises(UserError):
            self.project.status = 'reconciled'

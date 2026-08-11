# -*- coding: utf-8 -*-
from odoo import models, fields, api


class QbObjectCatalogue(models.Model):
    _name = 'qb.object.catalogue'
    _description = 'QuickBooks Object Catalogue Entry (FR-011)'
    # FR-006: Rate-limit governed QBO extraction via Accounting API + Batch endpoint
    # FR-007: Batch extraction with rate-limit handling
    # FR-008: Pagination via query position cursor (resumable extraction)
    # FR-009: Persisted extraction cursor for resumability
    # FR-010: QBDT ingestion from uploaded exports
    # FR-011: Configurable object catalogue
    # FR-012: Default object catalogue with all entries shipped
    # FR-013: QBO entity name and Reports API endpoint configuration

    # FR-011: Configurable object catalogue
    name = fields.Char(
        string='Object Name',
        required=True,
        help='Descriptive name for the object type'
    )
    object_type = fields.Selection(
        [
            ('companyinfo', 'CompanyInfo'),
            ('companycurrency', 'CompanyCurrency'),
            ('exchangerate', 'ExchangeRate'),
            ('account', 'Account'),
            ('taxcode', 'TaxCode'),
            ('taxrate', 'TaxRate'),
            ('taxagency', 'TaxAgency'),
            ('term', 'Term'),
            ('paymentmethod', 'PaymentMethod'),
            ('customer', 'Customer'),
            ('vendor', 'Vendor'),
            ('employee', 'Employee'),
            ('item', 'Item'),
            ('pricelevel', 'PriceLevel'),
            ('invoice', 'Invoice'),
            ('creditmemo', 'CreditMemo'),
            ('bill', 'Bill'),
            ('vendorcredit', 'VendorCredit'),
            ('payment', 'Payment'),
            ('billpayment', 'BillPayment'),
            ('salesreceipt', 'SalesReceipt'),
            ('refundreceipt', 'RefundReceipt'),
            ('estimate', 'Estimate'),
            ('purchaseorder', 'PurchaseOrder'),
            ('openingbalance', 'Opening Balance'),
            ('journalentry', 'JournalEntry'),
            ('deposit', 'Deposit'),
            ('transfer', 'Transfer'),
            ('bankaccount', 'BankAccount'),
            ('inventory', 'Inventory'),
            ('attachable', 'Attachable'),
            ('class', 'Class'),
            ('location', 'Location/Department'),
            ('custom', 'Custom'),
        ],
        string='Object Type',
        required=True
    )
    qbo_entity_name = fields.Char(
        string='QBO Entity Name',
        help='FR-013: QuickBooks API entity name (e.g., "Customer", "Invoice")'
    )
    qbo_reports_endpoint = fields.Char(
        string='QBO Reports Endpoint',
        help='FR-013: Optional Reports API endpoint (e.g., "TrialBalance") for reconciliation'
    )
    is_enabled = fields.Boolean(
        string='Enabled',
        default=True,
        help='FR-011: Enable/disable extraction of this object type'
    )
    extraction_method = fields.Selection(
        [('api', 'API'), ('reports_api', 'Reports API'), ('upload', 'Upload File')],
        string='Extraction Method',
        required=True,
        default='api',
        help='Method to extract this object type'
    )
    filter_open_only = fields.Boolean(
        string='Open Only',
        default=False,
        help='FR-011: Filter to open transactions only'
    )
    filter_active_only = fields.Boolean(
        string='Active Only',
        default=True,
        help='FR-011: Filter to active records only'
    )
    date_from = fields.Date(
        string='From Date',
        help='FR-011: Optional date range filter for extraction'
    )
    date_to = fields.Date(
        string='To Date',
        help='FR-011: Optional date range filter for extraction'
    )
    page_size = fields.Integer(
        string='Page Size',
        default=1000,
        help='FR-011: API page size for pagination'
    )
    last_cursor = fields.Char(
        string='Last Cursor',
        help='FR-009: Persisted cursor for resumable extraction'
    )
    target_model = fields.Char(
        string='Target Odoo Model',
        help='e.g., res.partner, product.product, account.move'
    )
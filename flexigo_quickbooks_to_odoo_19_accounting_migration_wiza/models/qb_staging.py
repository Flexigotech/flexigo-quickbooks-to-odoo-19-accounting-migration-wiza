# -*- coding: utf-8 -*-
from odoo import models, fields, api


class QbStaging(models.Model):
    _name = 'qb.staging'
    _description = 'QuickBooks Staging Record Enhancer (FR-010/012)'
    _inherit = 'migration.staging.record'
    # FR-018: Pre-load validation rules (required field, referential integrity, duplicate detection, etc.)
    # FR-019: Journal entry balance validation (sum(debit) = sum(credit) per entry)
    # FR-020: Dry-run execution without committing (wraps in savepoint, rolls back on completion)
    # FR-021: Validation results exportable to CSV

    # FR-010/012: Entity-specific normalized columns for QuickBooks
    # QBO-specific fields
    entity_id = fields.Char(
        string='QuickBooks Entity Id',
        help='QBO Id (unique within realm)'
    )
    sync_token = fields.Char(
        string='Sync Token',
        help='QBO SyncToken for optimistic locking'
    )
    last_updated_time = fields.Datetime(
        string='Last Updated Time (UTC)',
        help='FR-009/046: QBO MetaData.LastUpdatedTime normalized to UTC'
    )
    # QBDT-specific fields
    list_id = fields.Char(
        string='QBDT ListID',
        help='QuickBooks Desktop ListID (master records)'
    )
    txn_id = fields.Char(
        string='QBDT TxnID',
        help='QuickBooks Desktop TxnID (transactions)'
    )
    txn_number = fields.Char(
        string='Transaction Number',
        help='QBDT transaction number/reference'
    )
    # Common normalized fields for easier loading
    account_ref = fields.Char(
        string='Account Reference',
        help='Normalized account code/reference'
    )
    customer_ref = fields.Char(
        string='Customer Reference'
    )
    vendor_ref = fields.Char(
        string='Vendor Reference'
    )
    item_ref = fields.Char(
        string='Item Reference'
    )
    class_ref = fields.Char(
        string='Class Reference'
    )
    location_ref = fields.Char(
        string='Location/Department Reference'
    )
    currency_ref = fields.Char(
        string='Currency Reference'
    )
    tax_code_ref = fields.Char(
        string='Tax Code Reference'
    )
    debit_amount = fields.Float(
        string='Debit Amount'
    )
    credit_amount = fields.Float(
        string='Credit Amount'
    )
    quantity = fields.Float(
        string='Quantity'
    )
    unit_price = fields.Float(
        string='Unit Price'
    )
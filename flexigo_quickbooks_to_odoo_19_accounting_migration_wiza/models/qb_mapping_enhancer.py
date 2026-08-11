# -*- coding: utf-8 -*-
from odoo import models, fields, api


class QbMappingEnhancer(models.Model):
    _name = 'qb.mapping.enhancer'
    _description = 'QuickBooks Mapping Enhancer (FR-014/015/016)'
    # FR-014: Declarative field-mapping engine
    # FR-015: Value-mapping tables for QB enumeration values
    # FR-016: Starter mapping for QBO schema defaults
    # FR-017: Unmapped required values block affected records (validation report)

    # Default starter mapping for QBO schema (FR-016)
    # This provides the default mapping set that ships with the module
    name = fields.Char(
        string='Name',
        default='QuickBooks Default Mapping',
        required=True
    )
    version = fields.Char(
        string='Version',
        default='1.0',
        required=True
    )
    is_active = fields.Boolean(
        string='Active',
        default=True
    )


class QbMappingDefaultValue(models.Model):
    _name = 'qb.mapping.default.value'
    _description = 'QuickBooks Default Value Mappings'

    # FR-014/015: Default mappings for QBO enumeration values
    category = fields.Selection(
        [
            ('account_type', 'Account Type'),
            ('tax_code', 'Tax Code'),
            ('payment_term', 'Payment Term'),
            ('currency', 'Currency'),
            ('product_type', 'Product Type'),
            ('payment_method', 'Payment Method'),
        ],
        string='Category',
        required=True
    )
    source_value = fields.Char(
        string='Source Value (QuickBooks)',
        required=True
    )
    target_model = fields.Char(
        string='Target Model',
        required=True
    )
    target_field = fields.Char(
        string='Target Field'
    )
    default_code = fields.Char(
        string='Default Target Code'
    )
    transform_hint = fields.Char(
        string='Transform Hint',
        help='Suggested transform expression for this mapping'
    )


class QbDimensionDefaultMap(models.Model):
    _name = 'qb.dimension.default.map'
    _description = 'QuickBooks Class/Location Default Mapping (FR-041/042)'

    # FR-041/042: Default mappings for Class and Location dimensions
    dimension_type = fields.Selection(
        [('class', 'Class'), ('location', 'Location/Department')],
        string='Dimension Type',
        required=True
    )
    source_value = fields.Char(
        string='Source Value',
        required=True
    )
    analytic_plan_id = fields.Many2one(
        'account.analytic.plan',
        string='Default Analytic Plan'
    )
    analytic_account_id = fields.Many2one(
        'account.analytic.account',
        string='Default Analytic Account'
    )
    do_not_migrate = fields.Boolean(
        string='Do Not Migrate',
        default=False
    )
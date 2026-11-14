# -*- coding: utf-8 -*-
{
    'author': 'FlexigoTech',
    'images': ["static/description/fgt/cover.gif"],
    'support': 'comercial@flexigotech.com',
    'name': 'QuickBooks Migration Odoo',
    'version': '17.0.1.1.1',
    'category': 'Accounting/Data Migration',
    'license': 'OPL-1',
    'price': 9.0,
    'currency': 'EUR',
    'summary': 'Move QuickBooks Online into Odoo 17 once, at a cut-off date. Needs FlexigoTech Cloud subscription (from 49 EUR/month, VAT excl.). Moves your QuickBooks Online books into Odoo 17 at a cut-off date: master data, one opening entry with the open items, stock and a reconciliation account by account.',
    'depends': [
        'flexigotech_cloud',
        'flexigo_migration_base',
        'base',
        'account',
        'sale_management',
        'purchase',
        'stock',
        'stock_account',
    ],
    'data': [
        'security/flexigo_quickbooks_security.xml',
        'security/ir.model.access.csv',
        'data/qb_object_catalogue_data.xml',
        'views/qb_connection_views.xml',
        'views/qb_oauth_templates.xml',
        'views/qb_object_catalogue_views.xml',
    ],
    'uninstall_hook': 'uninstall_hook',
    'application': True,
    'installable': True,
    'auto_install': False,
    'description': """
QuickBooks to Odoo 17 Accounting Migration Wizard
=================================================
A wizard-driven, auditable, idempotent toolkit for extracting accounting data from
QuickBooks Online (via the Intuit Accounting API) and loading it into Odoo 17 with a
repeatable, reconciled cutover process.

New connections reach QuickBooks Online through FlexigoTech Cloud, which keeps
the client ID and secret of your Intuit app and the QuickBooks tokens encrypted,
and needs its trial or subscription (Settings > FlexigoTech Cloud). Without it
nothing is sent, and nothing in Odoo is deleted. Connections made before 1.1.0
keep working as bought, directly from Odoo.
    """,
    'external_dependencies': {
        'python': [
            'requests',
        ],
    },
}
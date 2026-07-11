# -*- coding: utf-8 -*-
{
    'images': [
        'static/description/banner.png',
        'static/description/screenshots-marketing/01-quickbooks-connections.png',
        'static/description/screenshots-marketing/02-qb-object-catalogue.png',
    ],
    'website': 'https://flexigotech.com',
    'support': 'comercial@flexigotech.com',
    'name': 'QuickBooks to Odoo 19 Accounting Migration Wizard',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Data Migration',
    'license': 'LGPL-3',
    'price': 0,
    'currency': 'EUR',
    'summary': 'Wizard-driven toolkit for QuickBooks → Odoo 19 cutover migrations',
    'depends': [
        'flexigo_migration_base',
        'base',
        'account',
        'sale_management',
        'purchase',
        'stock',
    ],
    'data': [
        'security/flexigo_quickbooks_security.xml',
        'security/ir.model.access.csv',
        'data/qb_object_catalogue_data.xml',
        'views/qb_connection_views.xml',
        'views/qb_object_catalogue_views.xml',
    ],
    'application': True,
    'installable': True,
    'auto_install': False,
    'description': """
QuickBooks to Odoo 19 Accounting Migration Wizard
=================================================
A wizard-driven, auditable, idempotent toolkit for extracting accounting data from
QuickBooks Online (via Intuit Accounting API) or QuickBooks Desktop (via export upload)
and loading it into Odoo 19 with a repeatable, reconciled cutover process.

This module is FREE (LGPL-3). The actual migration is delivered as a paid
FlexigoTech implementation service.

For service enquiries: https://flexigotech.com/services
    """,
    'external_dependencies': {
        'python': [
            'requests',
        ],
    },
}
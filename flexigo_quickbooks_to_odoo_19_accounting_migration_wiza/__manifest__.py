# -*- coding: utf-8 -*-
{
    'author': 'FlexigoTech',
    'images': [
        'static/description/banner.png',
        'static/description/screenshots-marketing/00-overview.png',
        'static/description/screenshots-marketing/02-qb-dimension-default-map.png',
        'static/description/screenshots-marketing/03-qb-mapping-default-value.png',
        'static/description/screenshots-marketing/04-qb-mapping-enhancer.png',
        'static/description/screenshots-marketing/05-qb-object-catalogue.png',
        'static/description/screenshots-marketing/06-qb-reconciliation-enhancer.png',
        'static/description/screenshots-marketing/07-qb-staging.png',
    ],
    'website': 'https://flexigotech.com',
    'support': 'comercial@flexigotech.com',
    'name': 'QuickBooks to Odoo Migration Wizard',
    'version': '19.0.1.0.1',
    'category': 'Accounting/Data Migration',
    'license': 'OPL-1',
    'price': 49,
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
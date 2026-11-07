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
    'support': 'comercial@flexigobe.com',
    'name': 'QuickBooks to Odoo Migration Wizard',
    'version': '19.0.1.1.0',
    'category': 'Accounting/Data Migration',
    'license': 'OPL-1',
    'price': 9.0,
    'currency': 'EUR',
    'summary': 'Wizard-driven toolkit for QuickBooks to Odoo 19 cutover migrations. Needs FlexigoTech Cloud '
               'subscription (from 49 EUR/month, VAT excl.).',
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
QuickBooks to Odoo 19 Accounting Migration Wizard
=================================================
A wizard-driven, auditable, idempotent toolkit for extracting accounting data from
QuickBooks Online (via Intuit Accounting API) or QuickBooks Desktop (via export upload)
and loading it into Odoo 19 with a repeatable, reconciled cutover process.

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
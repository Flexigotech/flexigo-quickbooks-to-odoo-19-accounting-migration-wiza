# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class QbScopingWizard(models.TransientModel):
    # FR-039: Scoping questionnaire capturing source type, transaction counts, multicurrency, etc.
    _name = 'qb.scoping.wizard'
    _description = 'QuickBooks Migration Scoping Questionnaire'

    # FR-039: Scoping questionnaire fields
    source_type = fields.Selection(
        [('qbo', 'QuickBooks Online'), ('qbdt', 'QuickBooks Desktop')],
        string='Source System',
        required=True,
        help='Is the data source QuickBooks Online or Desktop?'
    )
    estimated_transaction_count = fields.Integer(
        string='Estimated Transaction Count',
        help='Approximate number of transactions to migrate'
    )
    multicurrency_enabled = fields.Boolean(
        string='Multi-Currency Enabled',
        help='Does the source system use multiple currencies?'
    )
    class_tracking_enabled = fields.Boolean(
        string='Class Tracking Enabled',
        help='Are Classes (cost centers/projects) used in the source?'
    )
    location_tracking_enabled = fields.Boolean(
        string='Location Tracking Enabled',
        help='Are Locations tracked in the source?'
    )
    custom_lists_needed = fields.Boolean(
        string='Custom Lists Migration',
        help='Are custom lists (e.g., custom fields) needed in Odoo?'
    )
    history_depth_months = fields.Integer(
        string='Historical Data Depth (Months)',
        default=36,
        help='How many months of historical data to migrate?'
    )
    company_name = fields.Char(
        string='Customer Company Name',
        required=True
    )
    company_size = fields.Selection(
        [
            ('micro', 'Micro (1-10 employees)'),
            ('small', 'Small (11-50)'),
            ('medium', 'Medium (51-250)'),
            ('large', 'Large (251+)'),
        ],
        string='Company Size',
        help='Approximate employee count'
    )
    expected_go_live = fields.Date(
        string='Expected Go-Live Date',
        help='Target date for cutover to Odoo'
    )

    # FR-039: Generate engagement scoping summary
    def action_generate_summary(self):
        """FR-039: Generate engagement scoping summary"""
        self.ensure_one()

        # FR-039: Build scoping summary based on questionnaire inputs
        summary_lines = [
            f"=== Migration Scoping Summary ===",
            f"Customer: {self.company_name}",
            f"Company Size: {dict(self._fields['company_size'].selection).get(self.company_size, '')}",
            f"",
            f"Source System: {dict(self._fields['source_type'].selection).get(self.source_type, '')}",
            f"Estimated Transactions: {self.estimated_transaction_count or 'Not specified'}",
            f"Multi-Currency: {'Yes' if self.multicurrency_enabled else 'No'}",
            f"Class Tracking: {'Yes' if self.class_tracking_enabled else 'No'}",
            f"Location Tracking: {'Yes' if self.location_tracking_enabled else 'No'}",
            f"Custom Lists: {'Yes' if self.custom_lists_needed else 'No'}",
            f"Historical Data: {self.history_depth_months} months",
            f"Target Go-Live: {self.expected_go_live or 'Not specified'}",
            f"",
            f"Next Steps:",
            f"1. Contact FlexigoTech for a detailed scoping meeting",
            f"2. Review the mapping templates for your industry",
            f"3. Prepare QuickBooks export or OAuth credentials",
            f"",
            f"For service inquiries: https://flexigotech.com/services",
        ]

        summary_text = '\n'.join(summary_lines)

        # Create or update scoping record in the migration project
        # (This is just a summary display; actual project creation happens in migration flow)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'qb.scoping.summary',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_summary': summary_text,
                'default_source_type': self.source_type,
                'default_company_name': self.company_name,
            }
        }

    # FR-040: Link to FlexigoTech services
    def action_open_services_page(self):
        """FR-040: Open FlexigoTech services page"""
        return {
            'type': 'ir.actions.act_url',
            'url': 'https://flexigotech.com/services',
            'target': 'new',
        }


class QbScopingSummary(models.TransientModel):
    # Display scoping summary
    _name = 'qb.scoping.summary'
    _description = 'Scoping Summary'

    summary = fields.Text(
        string='Scoping Summary',
        readonly=True
    )
    source_type = fields.Char()
    company_name = fields.Char()

    def action_create_project(self):
        """Create a migration project from the scoping questionnaire"""
        # This would typically create a migration.project record
        # Commented out for now since migration.project is in the base module
        return {
            'type': 'ir.actions.act_url',
            'url': 'https://flexigotech.com/services',
            'target': 'new',
        }

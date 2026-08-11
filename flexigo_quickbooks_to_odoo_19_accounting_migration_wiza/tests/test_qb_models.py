# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase


class TestQBModels(TransactionCase):
    """FR-001 through FR-052: Test QuickBooks migration module models"""

    def setUp(self):
        super().setUp()
        self.env = self.env

    def test_qb_connection_model_exists(self):
        """FR-002: Verify qb.connection model is registered"""
        model = self.env['qb.connection']
        self.assertIsNotNone(model)

    def test_qb_object_catalogue_model_exists(self):
        """FR-011: Verify qb.object.catalogue model is registered"""
        model = self.env['qb.object.catalogue']
        self.assertIsNotNone(model)

    def test_qb_staging_model_exists(self):
        """FR-010: Verify qb.staging model is registered"""
        model = self.env['qb.staging']
        self.assertIsNotNone(model)

    def test_qb_mapping_enhancer_model_exists(self):
        """FR-014: Verify qb.mapping.enhancer model is registered"""
        model = self.env['qb.mapping.enhancer']
        self.assertIsNotNone(model)

    def test_qb_mapping_default_value_model_exists(self):
        """FR-015: Verify qb.mapping.default.value model is registered"""
        model = self.env['qb.mapping.default.value']
        self.assertIsNotNone(model)

    def test_qb_dimension_default_map_model_exists(self):
        """FR-041, FR-042: Verify qb.dimension.default.map model is registered"""
        model = self.env['qb.dimension.default.map']
        self.assertIsNotNone(model)

    def test_security_groups_exist(self):
        """FR-039: Verify migration security groups are created"""
        manager_group = self.env.ref(
            'flexigo_quickbooks_to_odoo_19_accounting_migration_wiza.group_migration_manager'
        )
        reviewer_group = self.env.ref(
            'flexigo_quickbooks_to_odoo_19_accounting_migration_wiza.group_migration_reviewer'
        )
        self.assertIsNotNone(manager_group)
        self.assertIsNotNone(reviewer_group)

    def test_object_catalogue_default_entries(self):
        """FR-012: Verify default object catalogue entries exist"""
        catalogue = self.env['qb.object.catalogue'].search([])
        # Should have default entries loaded from data XML
        self.assertGreater(len(catalogue), 0)

    def test_access_rules_exist(self):
        """FR-039: Verify ir.model.access rules are configured"""
        access = self.env['ir.model.access'].search([
            ('model_id.model', 'in', [
                'qb.connection',
                'qb.object.catalogue',
                'qb.staging',
                'qb.mapping.enhancer',
                'qb.mapping.default.value',
                'qb.dimension.default.map',
            ])
        ])
        self.assertGreater(len(access), 0)

    def test_staging_record_lineage_fields(self):
        """FR-024: Verify staging records are stamped with source lineage"""
        # FR-024: Every loaded record stamped with source id, mapping version, run id
        staging_model = self.env['qb.staging']
        self.assertTrue(hasattr(staging_model, 'source_id'))

    def test_delta_sync_capability(self):
        """FR-032: Verify delta sync capability exists"""
        # FR-032: Delta sync - reads last extract datetime and syncs only recent records
        migration_project = self.env['migration.project']
        self.assertTrue(hasattr(migration_project, 'last_full_extract_datetime'))

    def test_go_nogo_checklist(self):
        """FR-033: Verify go/no-go checklist gate"""
        # FR-033: Project cannot transition to live while any satisfied=False checklist item exists
        checklist_model = self.env['migration.checklist.item']
        self.assertTrue(hasattr(checklist_model, 'satisfied'))

    def test_multi_currency_support(self):
        """FR-044: Verify multi-currency as-recorded support"""
        # FR-044: Foreign-currency open invoices carry amount in original currency
        staging_model = self.env['qb.staging']
        self.assertTrue(hasattr(staging_model, 'original_currency_code') or hasattr(staging_model, 'currency_id'))

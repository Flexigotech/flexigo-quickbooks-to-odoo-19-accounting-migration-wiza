# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

MGR = 'flexigo_migration_base.group_migration_manager'

# model -> (values to create a record, field written in the test)
TABLES = {
    'qb.object.catalogue': ({'name': 'QB ACL object', 'object_type': 'custom', 'extraction_method': 'api'}, 'name'),
    'qb.staging': ({'object_type': 'class', 'source_id': 'QB-ACL'}, 'source_id'),
    'qb.mapping.enhancer': ({'name': 'QB ACL mapping'}, 'name'),
    'qb.mapping.default.value': ({'category': 'tax_code', 'source_value': 'QB-ACL', 'target_model': 'account.tax'},
                                 'source_value'),
    'qb.dimension.default.map': ({'dimension_type': 'class', 'source_value': 'QB-ACL'}, 'source_value'),
}


@tagged('post_install', '-at_install')
class TestQbAccess(TransactionCase):
    """Cola 259: the QuickBooks catalogue, staging rows, mappings and dimension maps are written by a Migration
    Manager only; any other internal user reads them (before, every internal user could create, edit and delete
    them, while the connection was already manager-only)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.internal = new_test_user(cls.env, login='qb-acl-internal', groups='base.group_user')
        cls.manager = new_test_user(cls.env, login='qb-acl-manager', groups=MGR)
        cls.project = cls.env['migration.project'].create({
            'name': 'QB ACL', 'source_type': 'qbo', 'company_id': cls.env.company.id,
            'owner_id': cls.env.user.id, 'status': 'draft'})
        cls.records = {model: cls.env[model].create(cls._values(model, 'seed')) for model in TABLES}

    @classmethod
    def _values(cls, model, suffix):
        values, field = TABLES[model]
        values = dict(values, **{field: '%s %s' % (values[field], suffix)})
        if model == 'qb.staging':
            values['project_id'] = cls.project.id
        return values

    def test_internal_user_reads_but_does_not_create_edit_or_delete(self):
        for model, record in self.records.items():
            field = TABLES[model][1]
            with self.subTest(model=model):
                as_internal = record.with_user(self.internal)
                self.assertEqual(as_internal.read([field])[0][field], record[field])
                with self.assertRaises(AccessError):
                    self.env[model].with_user(self.internal).create(self._values(model, 'internal'))
                with self.assertRaises(AccessError):
                    as_internal.write({field: 'changed by an internal user'})
                with self.assertRaises(AccessError):
                    as_internal.unlink()
                self.assertTrue(record.exists())

    def test_migration_manager_creates_edits_and_deletes(self):
        for model in TABLES:
            field = TABLES[model][1]
            with self.subTest(model=model):
                record = self.env[model].with_user(self.manager).create(self._values(model, 'manager'))
                record.write({field: 'changed by the manager'})
                self.assertEqual(record[field], 'changed by the manager')
                record.unlink()
                self.assertFalse(record.exists())

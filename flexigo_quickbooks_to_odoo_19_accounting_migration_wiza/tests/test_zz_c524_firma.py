# -*- coding: utf-8 -*-
"""Cola 524, delta of the R-k (N-1, N-4): the reviewer signs the figures of a reconciliation line. When a signed figure
changes afterwards (someone edits the line, or a new load brings other amounts), the signature falls and the line has
to be signed again; the same figures keep it. Duplicating a signed line gives an unsigned copy."""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from .test_fgt_cloud import MGR, MODULE

REVIEWER = MODULE + '.group_migration_reviewer'


@tagged('post_install', '-at_install')
class TestC524SignedFigures(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env['migration.project'].create({'name': 'C524 signed', 'source_type': 'qbo'})
        cls.line = cls.env['migration.reconciliation'].create({
            'project_id': cls.project.id, 'reconciliation_type': 'trial_balance', 'account_code': '1010',
            'account_name': 'Cash', 'source_amount': 100.0, 'target_amount': 100.0, 'tolerance': 0.01})
        cls.reviewer = new_test_user(cls.env, login='c524-reviewer', groups='base.group_user,' + REVIEWER)
        cls.manager = new_test_user(cls.env, login='c524-manager', groups='base.group_user,' + MGR)

    def _signed(self):
        self.line.with_user(self.reviewer).action_qb_sign_off()
        self.assertEqual((self.line.signed_off, self.line.signed_off_by), (True, self.reviewer))
        return self.line

    def _unsigned(self):
        return (self.line.signed_off, self.line.signed_off_by, self.line.signed_off_date)

    def test_01_a_changed_figure_takes_the_signature_off(self):
        for vals in ({'target_amount': 1.0}, {'source_amount': 1.0}, {'tolerance': 500.0}, {'account_code': '1011'},
                     {'reconciliation_type': 'ar_aging'}):
            with self.subTest(vals=vals):
                self._signed().with_user(self.manager).write(vals)
                self.assertEqual(self._unsigned(), (False, self.env['res.users'], False))
                self.line.write({'source_amount': 100.0, 'target_amount': 100.0, 'tolerance': 0.01,
                                 'account_code': '1010', 'reconciliation_type': 'trial_balance'})

    def test_02_the_same_figures_and_the_notes_keep_it(self):
        self._signed().with_user(self.manager).write({
            'source_amount': 100.0, 'target_amount': 100.0, 'account_name': 'Cash and bank',
            'qb_reconciliation_notes': 'checked with the bank statement'})
        self.assertEqual((self.line.signed_off, self.line.signed_off_by), (True, self.reviewer))

    def test_03_a_new_load_with_other_amounts_takes_it_off(self):
        tie_out = self.env['qb.connection']._qb_tie_out
        self._signed()
        tie_out(self.project, 'trial_balance', '1010', 'Cash', 100.0, 100.0)
        self.assertEqual((self.line.signed_off, self.line.signed_off_by), (True, self.reviewer))
        tie_out(self.project, 'trial_balance', '1010', 'Cash', 100.0, 1.0)
        self.assertEqual((self._unsigned(), self.line.variance), ((False, self.env['res.users'], False), 99.0))

    def test_04_a_duplicate_of_a_signed_line_is_unsigned(self):
        copy = self._signed().with_user(self.manager).copy({'account_code': '1011'})
        self.assertEqual((copy.signed_off, copy.signed_off_by, copy.signed_off_date),
                         (False, self.env['res.users'], False))
        self.assertEqual((self.line.signed_off, self.line.signed_off_by), (True, self.reviewer))
        with self.assertRaises(ValidationError):  # the same account twice in a project, not an AccessError
            self.line.with_user(self.manager).copy()

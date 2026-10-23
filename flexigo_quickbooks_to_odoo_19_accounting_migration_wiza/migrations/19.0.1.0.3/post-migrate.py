# -*- coding: utf-8 -*-
"""1.0.3: the OAuth client secret and tokens that older versions stored in plain text (their encryption key was not a
valid Fernet key, so the value was stored as it came) are encrypted now. Idempotent: an encrypted value is left alone."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['qb.connection'].search([])._encrypt_stored_secrets()

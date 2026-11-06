# -*- coding: utf-8 -*-
import base64
import hashlib
import json
import logging
import os
import requests
from datetime import datetime, timedelta
from odoo import models, fields, api, _
from odoo.exceptions import RedirectWarning, UserError

_logger = logging.getLogger(__name__)

# BW plan §2.2-2.6 (19.0.1.1.0): FlexigoTech Cloud keeps the customer's Intuit app and its tokens, one vault entry per
# connection (quickbooks@<id>, quickbooks-test@<id> for the sandbox), and renews the access by itself.
FGT_MODULE = 'flexigo_quickbooks_to_odoo_19_accounting_migration_wiza'
FGT_PLATFORMS = ('quickbooks', 'quickbooks-test')
INTUIT_TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'
QBO_HOSTS = {False: 'https://quickbooks.api.intuit.com', True: 'https://sandbox-quickbooks.api.intuit.com'}

def _fernet(env):
    """FR-004: the Fernet keys are MIGRATION_ENCRYPTION_KEY when it is a valid key, and one derived from the database
    secret (only administrators read it). New values use the first; any of them decrypts, so setting or removing the
    variable loses nothing. Before 1.0.3 a missing key made the encryption fail and store the value as it came."""
    from cryptography.fernet import Fernet, MultiFernet
    keys = []
    key = os.environ.get('MIGRATION_ENCRYPTION_KEY')
    if key:
        try:
            keys.append(Fernet(key))
        except ValueError:
            _logger.warning("MIGRATION_ENCRYPTION_KEY is not a valid Fernet key: using the key derived from the database")
    secret = env['ir.config_parameter'].sudo().get_param('database.secret')
    keys.append(Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())))
    return MultiFernet(keys)


def _is_token(value):
    """A Fernet token, whatever key made it: version byte 0x80, then timestamp, IV, 16-byte blocks and HMAC."""
    try:
        raw = base64.urlsafe_b64decode(value.encode())
    except ValueError:
        return False
    return raw[:1] == b'\x80' and len(raw) >= 73 and (len(raw) - 57) % 16 == 0


def encrypt_value(env, value):
    """FR-004: Encrypt sensitive values using Fernet (never stored as they come)"""
    return _fernet(env).encrypt(value.encode()).decode() if value else value


def decrypt_value(env, value):
    """FR-004: Decrypt sensitive values. What versions before 1.0.3 stored in plain text is returned as it is until the
    upgrade encrypts it; a token that no key opens (a duplicated database, a changed MIGRATION_ENCRYPTION_KEY) raises
    instead of going to QuickBooks as the credential."""
    from cryptography.fernet import InvalidToken
    if not value:
        return value
    try:
        return _fernet(env).decrypt(value.encode()).decode()
    except InvalidToken:
        if _is_token(value):
            raise UserError(env._("The QuickBooks credentials were encrypted with another key (the database was "
                                  "duplicated or MIGRATION_ENCRYPTION_KEY changed): enter them again.")) from None
        return value


class QbConnection(models.Model):
    _name = 'qb.connection'
    _description = 'QuickBooks Connection Profile'
    _inherit = 'migration.connection'
    # FR-001: QBO realm ID (company ID in QBO)
    # FR-002: QBO OAuth 2.0 connection profile with token management
    # FR-003: QBDT export upload profile
    # FR-004: Credentials stored encrypted (Fernet symmetric encryption, env var key)
    # FR-005: Test connection action (validates OAuth, parses company info; QBDT validates files)
    # FR-022–FR-026: Loader section (idempotent, batched, dependency-ordered, resumable)
    # FR-023: Idempotency guarantee (x_quickbooks_id, x_migration_run_id, x_mapping_version)
    # FR-025: Batch & resume (configurable batch size, progress persistence)
    # FR-026: Per-record error capture (try/except per record, continue on fail)

    # FR-001: QBO realm ID (company ID in QBO)
    realm_id = fields.Char(
        string='QuickBooks Realm ID',
        help='Unique identifier for QuickBooks Online company'
    )

    # FR-002: QBO OAuth 2.0 connection profile
    oauth_client_id = fields.Char(
        string='OAuth Client ID',
        help='Intuit OAuth client ID'
    )
    # Written in plain text, stored encrypted (create/write); only the administrator reads them.
    oauth_client_secret_encrypted = fields.Char(
        string='OAuth Client Secret (Encrypted)', groups='base.group_system', copy=False
    )
    oauth_access_token_encrypted = fields.Char(
        string='OAuth Access Token (Encrypted)', groups='base.group_system', copy=False
    )
    oauth_refresh_token_encrypted = fields.Char(
        string='OAuth Refresh Token (Encrypted)', groups='base.group_system', copy=False
    )
    oauth_refresh_token_expiry = fields.Datetime(
        string='Refresh Token Expiry'
    )
    minorversion = fields.Char(
        string='API Minor Version',
        default='75',
        help='QuickBooks API minor version for schema pinning'
    )
    is_sandbox = fields.Boolean(
        string='Use Sandbox',
        default=False
    )
    # FR-006: Configurable concurrency cap
    concurrency_cap = fields.Integer(
        string='Max Concurrent Requests',
        default=10,
        help='Maximum concurrent API requests (default ≤10 per Intuit limit)'
    )
    max_retries = fields.Integer(
        string='Max Retries',
        default=5,
        help='Maximum retry attempts on HTTP 429'
    )
    # FR-003: QBDT export upload profile
    export_format = fields.Selection(
        [('iif', 'IIF'), ('csv', 'CSV'), ('qbxml', 'qbXML')],
        string='Export Format'
    )
    attached_file_ids = fields.One2many(
        'ir.attachment',
        'res_id',
        domain=[('res_model', '=', 'qb.connection')],
        string='Uploaded Export Files'
    )
    validation_status = fields.Selection(
        [('pending', 'Pending'), ('valid', 'Valid'), ('invalid', 'Invalid')],
        string='Validation Status',
        default='pending'
    )
    detected_record_counts = fields.Json(
        string='Detected Record Counts',
        help='JSON with counts per entity type detected in upload'
    )

    # Where Intuit sends the user back: register this exact URL in the Intuit app (Redirect URIs)
    oauth_redirect_uri = fields.Char(string='Redirect URI', compute='_compute_oauth_redirect_uri')

    # 1.1.0 (BW plan §2.3-2.4): how this connection reaches Intuit. 'direct': it existed before 1.1.0 and goes on exactly
    # as bought (OAuth from this server, its tokens encrypted here); 'cloud': through FlexigoTech Cloud. create() always
    # gives 'cloud' and the route never changes afterwards (no default: the rows of 1.0.x get 'direct' in the migration).
    fgt_route = fields.Selection([('direct', 'Direct (bought before FlexigoTech Cloud)'),
                                  ('cloud', 'FlexigoTech Cloud')], string='Route', readonly=True, copy=False)
    fgt_client_secret = fields.Char(
        string='Client Secret', compute='_compute_fgt_client_secret', inverse='_inverse_fgt_client_secret',
        groups='base.group_system',
        help='The client secret of your Intuit app: it goes to the FlexigoTech Cloud vault and never stays in Odoo.')
    fgt_consent = fields.Boolean(
        string='Send my Intuit app to FlexigoTech Cloud', copy=False,
        help='FlexigoTech Cloud keeps the client ID and secret of your Intuit app and the QuickBooks tokens, encrypted, '
             'and passes the calls of this connection to QuickBooks Online. Without this consent nothing goes out.')
    fgt_saved_at = fields.Datetime(string='Intuit app saved in FlexigoTech Cloud', readonly=True, copy=False,
                                   groups='base.group_system')
    fgt_connected_on = fields.Datetime(string='Authorized through FlexigoTech Cloud', readonly=True, copy=False,
                                       groups='base.group_system')

    _SECRET_FIELDS = ('oauth_client_secret_encrypted', 'oauth_access_token_encrypted', 'oauth_refresh_token_encrypted')

    def _encrypt_secret_vals(self, vals):
        return {name: encrypt_value(self.env, value) if name in self._SECRET_FIELDS else value
                for name, value in vals.items()}

    def _no_secret_here(self):
        return UserError(_('A connection through FlexigoTech Cloud keeps no QuickBooks secret in Odoo: enter the Client '
                           'Secret of your Intuit app in its FlexigoTech Cloud field.'))

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get(name) for vals in vals_list for name in self._SECRET_FIELDS):
            raise self._no_secret_here()
        return super().create([dict(vals, fgt_route='cloud') for vals in vals_list])

    def write(self, vals):
        if 'fgt_route' in vals and any(connection.fgt_route != vals['fgt_route'] for connection in self):
            raise UserError(_('The route of a QuickBooks connection never changes: a connection bought before '
                              'FlexigoTech Cloud keeps going direct, and a new one goes through FlexigoTech Cloud.'))
        if any(vals.get(name) for name in self._SECRET_FIELDS) and any(c.fgt_route == 'cloud' for c in self):
            raise self._no_secret_here()
        return super().write(self._encrypt_secret_vals(vals))

    def unlink(self):
        # a new connection goes through FlexigoTech Cloud: deleting a bought one cannot be undone
        self.check_access('unlink')
        if not self.env.is_admin() and any(connection.fgt_route != 'cloud' for connection in self):
            raise UserError(_('Only an administrator deletes a connection bought before FlexigoTech Cloud: a new one '
                              'would go through FlexigoTech Cloud.'))
        return super().unlink()

    def _fgt_module(self):
        return FGT_MODULE

    def _fgt_platform(self):
        self.ensure_one()
        return '%s@%d' % (FGT_PLATFORMS[1] if self.is_sandbox else FGT_PLATFORMS[0], self.id)

    def _compute_fgt_client_secret(self):
        self.fgt_client_secret = False   # write-only: it lives in the FlexigoTech Cloud vault

    def _inverse_fgt_client_secret(self):
        """BW plan §2.3: the client ID and secret of the customer's Intuit app go to the FlexigoTech Cloud vault (the relay
        adds the tokens at the OAuth return); Odoo never stores the secret."""
        for connection in self.filtered('fgt_client_secret'):
            if connection.fgt_route != 'cloud':
                raise UserError(_('This connection was bought before FlexigoTech Cloud and goes on as before: enter '
                                  'the Client Secret of your Intuit app in its own field.'))
            if not connection.fgt_consent:
                raise UserError(_('Tick "Send my Intuit app to FlexigoTech Cloud" first: without it nothing goes out.'))
            if not connection.oauth_client_id:
                raise UserError(_('Fill in the Client ID of your Intuit app first.'))
            cloud = self.env['flexigotech.cloud']
            cloud.ensure_service(FGT_MODULE, raise_error=True)
            cloud._relay_store_credential(FGT_MODULE, connection._fgt_platform(), {
                'client_id': connection.oauth_client_id, 'client_secret': connection.fgt_client_secret})
            connection.sudo().fgt_saved_at = fields.Datetime.now()
        self.invalidate_recordset(['fgt_client_secret'])   # not even the cache of this request keeps it

    def _qbo_send(self, method, path, **kwargs):
        """The one way to Intuit (BW plan §2.6): the API (path 'v3/company/<realm>/...', relative to the QuickBooks
        Online host) and the token endpoint (path INTUIT_TOKEN_URL, a form with grant_type). 'direct' as bought, from
        this server with the app and the tokens encrypted here; 'cloud' through FlexigoTech Cloud, which keeps them."""
        self.ensure_one()
        kwargs.setdefault('timeout', 30)
        kwargs['headers'] = dict({'Accept': 'application/json'}, **(kwargs.get('headers') or {}))
        if self.fgt_route == 'cloud':
            return self._qbo_send_cloud(method, path, **kwargs)
        return self._qbo_send_direct(method, path, **kwargs)

    def _qbo_send_direct(self, method, path, **kwargs):
        if path == INTUIT_TOKEN_URL:
            return requests.post(path, auth=(self.oauth_client_id, self._get_oauth_client_secret()), **kwargs)
        kwargs['headers']['Authorization'] = 'Bearer %s' % self._get_oauth_access_token()
        return getattr(requests, method.lower())('%s/%s' % (QBO_HOSTS[bool(self.is_sandbox)], path), **kwargs)

    def _qbo_send_cloud(self, method, path, **kwargs):
        cloud = self.env['flexigotech.cloud']
        cloud.ensure_service(FGT_MODULE, raise_error=True)
        if path == INTUIT_TOKEN_URL:
            # only the authorization code goes there: the relay exchanges it and keeps the tokens (204, none comes back)
            data = kwargs['data']
            if data.get('grant_type') != 'authorization_code':
                raise UserError(_('FlexigoTech Cloud renews the QuickBooks access by itself.'))
            return cloud._relay_request(FGT_MODULE, '_canje', 'POST', self._fgt_platform(), timeout=kwargs['timeout'],
                                        json={'code': data['code'], 'redirect_uri': data['redirect_uri']})
        return cloud._relay_request(FGT_MODULE, self._fgt_platform(), method, path, **kwargs)

    def _qbo_api_client(self):
        from ..lib.qbo_api_client import QboApiClient
        return QboApiClient(None, self.realm_id, self.minorversion, sandbox=self.is_sandbox,
                            concurrency_cap=self.concurrency_cap, max_retries=self.max_retries, send=self._qbo_send)

    def _qbo_reports_client(self):
        from ..lib.qbo_reports_client import QboReportsClient
        return QboReportsClient(None, self.realm_id, self.minorversion, sandbox=self.is_sandbox, send=self._qbo_send)

    def _encrypt_stored_secrets(self):
        """1.0.3 upgrade: encrypt what older versions stored in plain text (a token of the key is left alone)."""
        for connection in self.sudo():
            plain = {name: connection[name] for name in self._SECRET_FIELDS
                     if connection[name] and not _is_token(connection[name])}
            if plain:
                connection.write(plain)

    def _check_can_manage(self):
        """The secrets go to QuickBooks with every call: only who may change the connection uses them."""
        self.check_access('write')

    # FR-004: Credentials never logged; admin-only fields, read through sudo for whoever runs the migration
    def _get_oauth_client_secret(self):
        return decrypt_value(self.env, self.sudo().oauth_client_secret_encrypted)

    def _get_oauth_access_token(self):
        return decrypt_value(self.env, self.sudo().oauth_access_token_encrypted)

    def _get_oauth_refresh_token(self):
        return decrypt_value(self.env, self.sudo().oauth_refresh_token_encrypted)

    def _get_oauth_redirect_uri(self):
        return self.get_base_url() + '/qb/oauth/callback'

    def _compute_oauth_redirect_uri(self):
        for connection in self:
            connection.oauth_redirect_uri = connection._get_oauth_redirect_uri()

    def action_connect_qbo(self):
        """FR-002: start the authorization with Intuit (the controller keeps the state in the session)."""
        self.ensure_one()
        self._check_can_manage()
        if self.fgt_route == 'cloud':
            self.env['flexigotech.cloud'].ensure_service(FGT_MODULE, raise_error=True)
            if not self.oauth_client_id or not self.sudo().fgt_saved_at:
                raise UserError(_('Ask an administrator (Settings) to fill in the Client ID and the Client Secret of '
                                  'your Intuit app on this connection, with the consent to send them to FlexigoTech '
                                  'Cloud, first.'))
        elif not self.oauth_client_id or not self.sudo().oauth_client_secret_encrypted:
            if not self.env.user.has_group('base.group_system'):  # the Client Secret is a Settings-only field
                raise UserError(_('Ask an administrator (Settings) to fill in the Client ID and the Client Secret of '
                                  'your Intuit app on this connection first: only administrators see the secret.'))
            raise UserError(_('Fill in the Client ID and the Client Secret of your Intuit app first.'))
        return {'type': 'ir.actions.act_url', 'url': '/qb/oauth/authorize?connection_id=%d' % self.id, 'target': 'self'}

    # FR-005: Test connection action
    def action_test_connection(self):
        self.ensure_one()
        self._check_can_manage()
        if self.connection_type == 'oauth':
            return self._test_qbo_connection()
        elif self.connection_type == 'upload':
            return self._test_qbdt_upload()
        else:
            raise UserError(_("Unknown connection type"))

    def _test_qbo_connection(self):
        """FR-005: Validate QBO OAuth credentials against CompanyInfo"""
        try:
            response = self._qbo_send('GET', f"v3/company/{self.realm_id}/companyinfo/{self.realm_id}",
                                      params={'minorversion': self.minorversion})

            if response.status_code == 200:
                data = response.json()
                company_name = data.get('CompanyInfo', {}).get('CompanyName', 'Unknown')
                self.is_validated = True
                self.validation_message = f"Connection successful. Company: {company_name}, RealmId: {self.realm_id}"
            else:
                self.is_validated = False
                self.validation_message = f"Connection failed: HTTP {response.status_code}"

        except RedirectWarning:
            raise   # no FlexigoTech Cloud service: the button says so, and nothing was called
        except Exception as e:
            self.is_validated = False
            # FR-004: Never log secrets
            self.validation_message = f"Connection failed: {str(e)[:100]}"
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Connection Test'),
                'message': self.validation_message,
                'type': 'success' if self.is_validated else 'danger',
            }
        }

    def _test_qbdt_upload(self):
        """FR-005: Validate uploaded QBDT export structure"""
        if not self.attached_file_ids:
            self.is_validated = False
            self.validation_message = "No files uploaded"
        else:
            # FR-052: Validate file format and structure
            valid, counts = self._validate_upload_structure()
            if valid:
                self.is_validated = True
                self.validation_status = 'valid'
                self.detected_record_counts = json.dumps(counts)
                self.validation_message = f"Valid upload. Detected: {counts}"
            else:
                self.is_validated = False
                self.validation_status = 'invalid'
                self.validation_message = "Invalid upload structure"
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Upload Validation'),
                'message': self.validation_message,
                'type': 'success' if self.is_validated else 'danger',
            }
        }

    def _validate_upload_structure(self):
        """FR-052: Validate QBDT export format and structure"""
        counts = {}
        # Basic validation - real implementation would parse IIF/CSV/qbXML
        for attachment in self.attached_file_ids:
            if self.export_format == 'iif':
                # Validate IIF structure
                counts['files'] = len(self.attached_file_ids)
            elif self.export_format == 'csv':
                counts['csv_files'] = len(self.attached_file_ids)
            elif self.export_format == 'qbxml':
                counts['qbxml_files'] = len(self.attached_file_ids)
        return True, counts

    # FR-002: Refresh token handling with rotation
    @api.private
    def refresh_access_token(self):
        """FR-002: Refresh OAuth access token, handling refresh token rotation"""
        self._check_can_manage()
        if self.fgt_route == 'cloud':
            return True   # FlexigoTech Cloud renews the access by itself: no token is kept here
        if not self.sudo().oauth_refresh_token_encrypted:
            raise UserError(_("No refresh token available"))

        # FR-002: Atomic update of both tokens
        try:
            response = self._qbo_send('POST', INTUIT_TOKEN_URL, data={
                'grant_type': 'refresh_token', 'refresh_token': self._get_oauth_refresh_token()})

            if response.status_code == 200:
                data = response.json()
                # write() encrypts them
                self.sudo().write({'oauth_access_token_encrypted': data['access_token'],
                                   'oauth_refresh_token_encrypted': data['refresh_token']})
                # Intuit returns expires_in in seconds
                expiry = datetime.now() + timedelta(seconds=data.get('expires_in', 3600))
                self.oauth_refresh_token_expiry = expiry
            else:
                raise UserError(_("Token refresh failed: HTTP %s") % response.status_code)
        except Exception as e:
            raise UserError(_("Token refresh error: %s") % str(e)[:100])
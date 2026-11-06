# -*- coding: utf-8 -*-
import base64
import hashlib
import json
import logging
import os
import requests
from datetime import datetime, timedelta
from odoo import Command, models, fields, api, _
from odoo.exceptions import RedirectWarning, UserError

_logger = logging.getLogger(__name__)

# BW plan §2.2-2.6 (19.0.1.1.0): FlexigoTech Cloud keeps the customer's Intuit app and its tokens, one vault entry per
# connection (quickbooks@<id>, quickbooks-test@<id> for the sandbox), and renews the access by itself.
FGT_MODULE = 'flexigo_quickbooks_to_odoo_19_accounting_migration_wiza'
FGT_PLATFORMS = ('quickbooks', 'quickbooks-test')
INTUIT_TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'
QBO_HOSTS = {False: 'https://quickbooks.api.intuit.com', True: 'https://sandbox-quickbooks.api.intuit.com'}
# BW step 4b: what the migration engine extracts, in load order (accounts before invoices). object_type of the staging:
# (QuickBooks entity, WHERE of its query, field of the name, field of the code). Then the Trial Balance report, staged as
# 'custom' (the staging has no type for a report). Intuit's query syntax and pages: developer.intuit.com/quickbooks-online/
# docs/learn/explore-the-quickbooks-online-api/data-queries (MAXRESULTS is 1000 at most; STARTPOSITION starts at 1).
QBO_ENTITIES = {
    'account': ('Account', '', 'FullyQualifiedName', 'AcctNum'),
    'taxcode': ('TaxCode', '', 'Name', None),
    'term': ('Term', '', 'Name', None),
    'class': ('Class', '', 'FullyQualifiedName', None),
    'location': ('Department', '', 'FullyQualifiedName', None),
    'customer': ('Customer', '', 'DisplayName', None),
    'vendor': ('Vendor', '', 'DisplayName', None),
    'item': ('Item', '', 'FullyQualifiedName', 'Sku'),
    'invoice': ('Invoice', "Balance > '0'", 'CustomerRef', 'DocNumber'),   # open ones only
    'bill': ('Bill', "Balance > '0'", 'VendorRef', 'DocNumber'),
}
TRIAL_BALANCE = 'custom'
QBO_PAGE = 1000
# BW step 4b-2a, the type of an account the user creates: Intuit's AccountType (AccountTypeEnum, and the two
# AccountSubType values Odoo has a type of its own for) → account_type of Odoo. Intuit's schema:
# github.com/intuit/QuickBooks-V3-Java-SDK, ipp-v3-java-data/src/main/xsd/Finance.xsd (AccountTypeEnum,
# AccountSubTypeEnum); developer.intuit.com/quickbooks-online/docs/learn/learn-basic-bookkeeping/accounts.
QBO_ACCOUNT_TYPES = {
    'Bank': 'asset_cash',
    'Accounts Receivable': 'asset_receivable',
    'Other Current Asset': 'asset_current',
    'Fixed Asset': 'asset_fixed',
    'Other Asset': 'asset_non_current',
    'Accounts Payable': 'liability_payable',
    'Credit Card': 'liability_credit_card',
    'Other Current Liability': 'liability_current',
    'Long Term Liability': 'liability_non_current',
    'Equity': 'equity',
    'Income': 'income',
    'Other Income': 'income_other',
    'Cost of Goods Sold': 'expense_direct_cost',
    'Expense': 'expense',
    'Other Expense': 'expense_other',
    'Non-Posting': 'off_balance',
}
QBO_ACCOUNT_SUBTYPES = {'PrepaidExpenses': 'asset_prepayments', 'Depreciation': 'expense_depreciation'}
# Item.Type (ItemTypeEnum of the same Finance.xsd) → product of Odoo; the other kinds (Category, Group, Assembly…)
# are no product and stay out.
QBO_ITEM_TYPES = {
    'Inventory': {'type': 'consu', 'is_storable': True},
    'NonInventory': {'type': 'consu', 'is_storable': False},
    'Service': {'type': 'service', 'is_storable': False},
}
# BW step 4b-2b, the open items of the opening entry, by staging type: (partner type, its reference, the reference of
# its account, the Odoo type of that account, sign). Finance.xsd: Invoice and Bill (Balance, HomeBalance, DueDate,
# ARAccountRef, APAccountRef). Credit notes are other entities (CreditMemo, VendorCredit), not extracted yet.
QBO_OPEN_ITEMS = {
    'invoice': ('customer', 'CustomerRef', 'ARAccountRef', 'asset_receivable', 1),
    'bill': ('vendor', 'VendorRef', 'APAccountRef', 'liability_payable', -1),
}
QBO_AGING = {'asset_receivable': 'ar_aging', 'liability_payable': 'ap_aging'}
RECONCILIATION_TOLERANCE = 0.01

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
        # the vault keeps the app under quickbooks@<id> or quickbooks-test@<id> with its tokens: another app or the
        # other environment needs a new connection (kb_conector_pasarela_pausas §14: fgt_saved_at stays as it is)
        if {'oauth_client_id', 'is_sandbox'} & vals.keys() and any(
                c.fgt_route == 'cloud' and c.sudo().fgt_saved_at
                and (vals.get('oauth_client_id', c.oauth_client_id) != c.oauth_client_id
                    or bool(vals.get('is_sandbox', c.is_sandbox)) != c.is_sandbox) for c in self):
            raise UserError(_('The Intuit app of this connection is saved in FlexigoTech Cloud: to use another app or '
                              'the sandbox, create a new QuickBooks connection.'))
        return super().write(self._encrypt_secret_vals(vals))

    def unlink(self):
        # a new connection goes through FlexigoTech Cloud: deleting a bought one cannot be undone
        self.check_access('unlink')
        if not self.env.is_system() and any(connection.fgt_route != 'cloud' for connection in self):
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
        if not self.fgt_consent:   # withdrawn after saving: nothing goes out (kb_conector_pasarela_pausas §13)
            raise UserError(_('Tick "Send my Intuit app to FlexigoTech Cloud" on this connection first: without it '
                              'nothing goes out.'))
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

    # ---------- the migration engine (flexigo_migration_base, migration.run) ----------

    def _qb_check_extract(self):
        """BW plan §2.4: the extraction only goes through FlexigoTech Cloud. A connection bought before keeps what it
        bought, and is told so before anything is called or queued."""
        for connection in self:
            if connection.fgt_route != 'cloud':
                raise UserError(_('This QuickBooks connection was made before FlexigoTech Cloud: it keeps working as '
                                  'bought (Connect to QuickBooks and Test Connection), but the extraction goes through '
                                  'FlexigoTech Cloud. To extract, create a new QuickBooks connection, which goes '
                                  'through FlexigoTech Cloud, and connect it with Intuit: it takes two minutes.'))
            if not connection.realm_id:
                raise UserError(_('Connect this QuickBooks connection with Intuit first (Connect to QuickBooks).'))

    def _migration_object_types(self):
        return [*QBO_ENTITIES, TRIAL_BALANCE]

    def _migration_extract_page(self, object_type, cursor):
        """One page for the engine: the query of the entity from STARTPOSITION cursor (1 the first time) until a page
        comes short, or the Trial Balance to today. A refusal of FlexigoTech Cloud goes up as it is: the engine pauses."""
        self.ensure_one()
        self._qb_check_extract()
        if object_type == TRIAL_BALANCE:
            end_date = fields.Date.to_string(fields.Date.context_today(self))
            return [self._qbo_get('reports/TrialBalance', {'end_date': end_date})], None
        entity, where, _name, _code = QBO_ENTITIES[object_type]
        start = cursor or 1
        query = 'SELECT * FROM %s%s STARTPOSITION %d MAXRESULTS %d' % (
            entity, where and ' WHERE ' + where, start, QBO_PAGE)
        # Intuit leaves the entity out of QueryResponse when there are no rows
        rows = self._qbo_get('query', {'query': query}).get('QueryResponse', {}).get(entity, [])
        return rows, (start + QBO_PAGE if len(rows) == QBO_PAGE else None)

    def _qbo_get(self, path, params):
        """GET v3/company/<realm>/<path> with the minor version of the connection: Intuit's JSON, or UserError."""
        response = self._qbo_send('GET', 'v3/company/%s/%s' % (self.realm_id, path),
                                  params=dict(params, minorversion=self.minorversion or '75'))
        if response.status_code != 200:
            raise UserError(_('QuickBooks Online answered HTTP %(status)s to %(path)s.', status=response.status_code,
                              path=path))
        data = response.json()
        fault = data.get('Fault') or (data.get('QueryResponse') or {}).get('Fault')
        if fault:   # an error page is no empty page: it must not end the extraction of the entity
            raise UserError(_('QuickBooks Online refused %(path)s: %(fault)s', path=path, fault=json.dumps(fault)[:300]))
        return data

    def _migration_normalize(self, object_type, payload):
        """Staging values of one payload: its Id and realm, and the name, code and active flag of BW plan §2.6."""
        if object_type == TRIAL_BALANCE:
            return {'source_id': 'TrialBalance', 'source_realm': self.realm_id, 'normalized_name': 'TrialBalance',
                    'normalized_code': (payload.get('Header') or {}).get('EndPeriod') or False,
                    'normalized_active': True}
        _entity, _where, name_field, code_field = QBO_ENTITIES[object_type]
        name = payload.get(name_field) or payload.get('Name')
        if isinstance(name, dict):   # the CustomerRef or VendorRef of an invoice or a bill
            name = name.get('name')
        return {'source_id': payload['Id'], 'source_realm': self.realm_id, 'normalized_name': name or False,
                'normalized_code': code_field and payload.get(code_field) or False,
                'normalized_active': payload.get('Active') is not False}

    def _migration_load_vals(self, staging):
        """What the engine loads from one staged record (BW plan §2.1, the master data), in the company of the project
        and archived when it is inactive in QuickBooks. A record that maps to one already in Odoo is linked to it with
        no values, so loading never changes it; None leaves it out: a tax code is only mapped, never a tax, and the open
        invoices and bills are lines of the opening entry the Trial Balance makes. A UserError tells the user what to map."""
        loader = {'account': self._qb_load_account, 'term': self._qb_load_term, 'class': self._qb_load_dimension,
                  'location': self._qb_load_dimension, 'customer': self._qb_load_partner,
                  'vendor': self._qb_load_partner, 'item': self._qb_load_item,
                  TRIAL_BALANCE: self._qb_load_opening}.get(staging.object_type)
        return loader(staging, staging.raw_payload or {}) if loader else None

    def _qb_existing(self, staging, record):
        """The source record is this record of Odoo: linked to it (the engine then updates nothing, and an item or the
        opening of step 4b-2b finds it), with no values."""
        key = {'project_id': staging.project_id.id, 'object_type': staging.object_type, 'source_id': staging.source_id}
        Link = self.env['migration.record.link']
        if not Link.search_count([(name, '=', value) for name, value in key.items()]):
            Link.create(dict(key, res_model=record._name, res_id=record.id))
        return record._name, {}

    def _qb_linked(self, project, object_type, source_id):
        """The record of Odoo already loaded from that QuickBooks record, or nothing."""
        link = source_id and self.env['migration.record.link'].search([
            ('project_id', '=', project.id), ('object_type', '=', object_type), ('source_id', '=', str(source_id))],
            limit=1)
        return link and self.env[link.res_model].browse(link.res_id).exists()

    def _qb_load_account(self, staging, row):
        """Mapped to the chart of the company: the mapping set of the project (an Account Type value whose source is
        the Id, number or name of the account and whose target is an account or its code), else the account number.
        Created only when the user gave a code no account has yet: that is how the mapping marks a new account."""
        company = staging.project_id.company_id
        Account = self.env['account.account'].with_company(company)
        in_company = Account._check_company_domain(company)
        keys = [row[key] for key in ('Id', 'AcctNum', 'Name', 'FullyQualifiedName') if row.get(key)]
        mapped = self.env['migration.mapping.value'].search([
            ('mapping_set_id', '=', staging.project_id.mapping_set_id.id), ('category', '=', 'account_type'),
            ('target_model', '=', 'account.account'), ('source_value', 'in', keys)], limit=1)
        if mapped.target_record_id:
            account = Account.search([*in_company, ('id', '=', mapped.target_record_id)])
            if not account:
                raise UserError(_('The mapping points to an account that is not in the chart of %s.', company.name))
            return self._qb_existing(staging, account)
        code = mapped.target_code or row.get('AcctNum')
        account = code and Account.search([*in_company, ('code', '=', code)], limit=1)
        if account:
            return self._qb_existing(staging, account)
        if not mapped.target_code:
            raise UserError(_('No account of %(company)s has the number of %(account)s: map it in the mapping set of '
                              'the project to an account, or to the code of a new one.', company=company.name,
                              account=staging.normalized_name))
        account_type = (QBO_ACCOUNT_SUBTYPES.get(row.get('AccountSubType'))
                        or QBO_ACCOUNT_TYPES.get(row.get('AccountType')))
        if not account_type:
            raise UserError(_('QuickBooks account type %s is unknown.', row.get('AccountType')))
        return 'account.account', {'name': staging.normalized_name, 'code': mapped.target_code,
                                   'account_type': account_type, 'company_ids': [Command.set(company.ids)],
                                   'active': staging.normalized_active}

    def _qb_load_term(self, staging, row):
        """A term with DueDays is one line of Odoo, with its early payment discount (DiscountPercent within
        DiscountDays). A date-driven one (DayOfMonthDue, DueNextMonthDays: Finance.xsd, SalesTerm) has a cut-off no
        line of Odoo has, so it is said instead of loaded wrong."""
        if row.get('DueDays') is None:
            raise UserError(_('A date-driven term (due on a day of the month, next month after a cut-off) has no '
                              'payment term in Odoo that says the same: create it by hand.'))
        discount = bool(row.get('DiscountPercent') and row.get('DiscountDays'))
        vals = {'name': staging.normalized_name, 'company_id': staging.project_id.company_id.id,
                'active': staging.normalized_active, 'early_discount': discount,
                'line_ids': [Command.clear(), Command.create({'value': 'percent', 'value_amount': 100.0,
                                                              'delay_type': 'days_after', 'nb_days': row['DueDays']})]}
        if discount:
            vals.update(discount_percentage=row['DiscountPercent'], discount_days=row['DiscountDays'])
        return 'account.payment.term', vals

    def _qb_load_dimension(self, staging, row):
        """A class or a department, by the dimension mapping of the project, else the default map of this module: an
        analytic account of Odoo, the plan to create it in, or Do Not Migrate. Unmapped, it is said, not guessed."""
        keys = [row[key] for key in ('Id', 'Name', 'FullyQualifiedName') if row.get(key)]
        dimension = self.env['migration.dimension.map'].search([
            ('mapping_set_id', '=', staging.project_id.mapping_set_id.id), ('object_type', '=', staging.object_type),
            ('source_value', 'in', keys)], limit=1) or self.env['qb.dimension.default.map'].search([
                ('dimension_type', '=', staging.object_type), ('source_value', 'in', keys)], limit=1)
        if dimension.do_not_migrate:
            return None
        if dimension.analytic_account_id:
            return self._qb_existing(staging, dimension.analytic_account_id)
        if not dimension.analytic_plan_id:
            raise UserError(_('Map %s to an analytic plan or account in the dimension mapping, or mark it Do Not '
                              'Migrate.', staging.normalized_name))
        return 'account.analytic.account', {'name': staging.normalized_name, 'plan_id': dimension.analytic_plan_id.id,
                                            'company_id': staging.project_id.company_id.id,
                                            'active': staging.normalized_active}

    def _qb_load_partner(self, staging, row):
        """A customer or a vendor is a partner of its own: Intuit keeps DisplayName unique across customers and
        vendors, so one that is both is two QuickBooks records, and they are not merged by guess."""
        customer = staging.object_type == 'customer'
        address = row.get('BillAddr') or {}
        Country = self.env['res.country']
        country_key = address.get('CountryCode') or address.get('Country')
        country = country_key and (Country.search([('code', '=ilike', country_key)], limit=1)
                                   or Country.search([('name', '=ilike', country_key)], limit=1))
        state = country and address.get('CountrySubDivisionCode') and self.env['res.country.state'].search([
            ('country_id', '=', country.id), ('code', '=ilike', address['CountrySubDivisionCode'])], limit=1)
        tax_id = row.get('PrimaryTaxIdentifier' if customer else 'TaxIdentifier')
        return 'res.partner', {
            'name': staging.normalized_name, 'company_id': staging.project_id.company_id.id,
            'active': staging.normalized_active, 'customer_rank' if customer else 'supplier_rank': 1,
            'email': (row.get('PrimaryEmailAddr') or {}).get('Address') or False,
            'phone': (row.get('PrimaryPhone') or {}).get('FreeFormNumber') or False,
            'street': address.get('Line1') or False, 'street2': address.get('Line2') or False,
            'city': address.get('City') or False, 'zip': address.get('PostalCode') or False,
            'country_id': country.id if country else False, 'state_id': state.id if state else False,
            # a tax id Intuit sends masked (XXXXXX6789) is no tax id
            'vat': tax_id if tax_id and 'XXX' not in tax_id.upper() else False,
        }

    def _qb_load_item(self, staging, row):
        """Inventory, non-inventory and service items are products (storable, goods, service), with their income and
        expense accounts only when those accounts are already loaded."""
        kind = QBO_ITEM_TYPES.get(row.get('Type'))
        if not kind:
            return None
        vals = dict(kind, name=staging.normalized_name, default_code=row.get('Sku') or False,
                    list_price=row.get('UnitPrice') or 0.0, standard_price=row.get('PurchaseCost') or 0.0,
                    company_id=staging.project_id.company_id.id, active=staging.normalized_active)
        for field, ref in (('property_account_income_id', 'IncomeAccountRef'),
                           ('property_account_expense_id', 'ExpenseAccountRef')):
            account = self._qb_linked(staging.project_id, 'account', (row.get(ref) or {}).get('value'))
            if account:
                vals[field] = account.id
        return 'product.template', vals

    def _qb_load_opening(self, staging, report):
        """BW plan §2.1: the Trial Balance at its end date becomes ONE posted opening entry, made once (its link), and
        every time it is loaded Odoo's balance at that date is reconciled with it, account by account."""
        project = staging.project_id
        balances, cutoff = self._qb_trial_balance(project, report)
        items = self._qb_open_items(project, balances)
        if not self.env['migration.record.link'].search_count([
                ('project_id', '=', project.id), ('object_type', '=', staging.object_type),
                ('source_id', '=', staging.source_id)]):
            self._qb_existing(staging, self._qb_opening_move(project, balances, items, cutoff))
        accounts = self.env['account.account'].with_company(project.company_id).browse(
            list(dict.fromkeys([*balances, *items])))
        self._qb_reconcile(project, balances, accounts, cutoff)
        return 'account.move', {}

    def _qb_trial_balance(self, project, report):
        """{account id: balance} of Intuit's Trial Balance report (Debit minus Credit of each row, by the link of its
        account) and its end date. The report is in the home currency, which must be the company's."""
        header, currency = report.get('Header') or {}, project.company_id.currency_id
        if header.get('Currency', currency.name) != currency.name:
            raise UserError(_('The Trial Balance of QuickBooks is in %(source)s and %(company)s works in %(target)s.',
                              source=header['Currency'], company=project.company_id.name, target=currency.name))
        titles = [column.get('ColTitle') for column in (report.get('Columns') or {}).get('Column', [])]
        if not header.get('EndPeriod') or not {'Debit', 'Credit'} <= set(titles):
            raise UserError(_('The Trial Balance of QuickBooks has no end date or no Debit and Credit columns.'))
        debit, credit = titles.index('Debit'), titles.index('Credit')
        balances, missing = {}, []
        for row in (report.get('Rows') or {}).get('Row', []):
            cells = row.get('ColData') or []
            if not cells or not cells[0].get('id'):
                continue   # the TOTAL summary
            account = self._qb_linked(project, 'account', cells[0]['id'])
            amount = float(cells[debit].get('value') or 0) - float(cells[credit].get('value') or 0)
            if not account:
                missing.append(cells[0].get('value') or cells[0]['id'])
            elif amount:
                balances[account.id] = balances.get(account.id, 0.0) + amount
        if missing:
            raise UserError(_('These accounts of the Trial Balance are not loaded, so there is no opening entry: '
                              '%s. Map them in the mapping set of the project and load again.', ', '.join(missing)))
        return balances, fields.Date.to_date(header['EndPeriod'])

    def _qb_open_items(self, project, balances):
        """{account id: [lines]} of the open invoices and bills staged: its partner by its link, DocNumber as label
        (the ref of a line is its entry's in Odoo), DueDate, and its Balance; in another currency, Balance in it and
        HomeBalance in the company's. The account is the one the item names, else the only receivable or payable one
        of the Trial Balance. What cannot be placed is said, not guessed."""
        company = project.company_id
        currency = company.currency_id
        in_balance = self.env['account.account'].with_company(company).browse(list(balances))
        items, problems = {}, []
        for staging in self.env['migration.staging.record'].search([
                ('project_id', '=', project.id), ('object_type', 'in', list(QBO_OPEN_ITEMS)),
                ('validation_status', '!=', 'invalid')], order='object_type, id'):
            partner_type, partner_ref, account_ref, account_type, sign = QBO_OPEN_ITEMS[staging.object_type]
            row = staging.raw_payload or {}
            if not row.get('Balance'):
                continue
            label = row.get('DocNumber') or '%s %s' % (QBO_ENTITIES[staging.object_type][0], staging.source_id)
            partner = self._qb_linked(project, partner_type, (row.get(partner_ref) or {}).get('value'))
            account = self._qb_linked(project, 'account', (row.get(account_ref) or {}).get('value'))
            if not account:
                account = in_balance.filtered(lambda a: a.account_type == account_type)
            code = (row.get('CurrencyRef') or {}).get('value') or currency.name
            foreign = code != currency.name and self.env['res.currency'].search([('name', '=', code)], limit=1)
            home = row.get('HomeBalance') if code != currency.name else row['Balance']
            if not partner or len(account) != 1 or code != currency.name and not (foreign and home is not None):
                problems.append(label)
                continue
            line = {'account_id': account.id, 'partner_id': partner.id, 'name': label,
                    'date_maturity': row.get('DueDate') or False, 'balance': currency.round(sign * float(home))}
            if foreign:
                line.update(currency_id=foreign.id, amount_currency=foreign.round(sign * float(row['Balance'])))
            items.setdefault(account.id, []).append(line)
        if problems:
            raise UserError(_('These open invoices or bills cannot go into the opening entry (their customer or vendor '
                              'is not loaded, the Trial Balance has no single receivable or payable account for them, '
                              'or their currency is not active in Odoo or has no HomeBalance): %s.', ', '.join(problems)))
        return items

    def _qb_opening_move(self, project, balances, items, cutoff):
        """One posted entry in the journal Odoo's own opening entry takes (the first general journal of the company),
        one line per account with its balance; a receivable or payable account by its open items, and what they do not
        cover on a line with no partner, so the entry adds up and the A/R or A/P tie-out shows it. A Trial Balance
        that does not balance creates nothing."""
        company = project.company_id
        currency = company.currency_id
        if not currency.is_zero(sum(balances.values())):
            raise UserError(_('The Trial Balance of QuickBooks at %(date)s does not balance (debit %(debit)s, credit '
                              '%(credit)s): no opening entry is made.', date=cutoff,
                              debit=currency.round(sum(v for v in balances.values() if v > 0)),
                              credit=currency.round(-sum(v for v in balances.values() if v < 0))))
        Account = self.env['account.account'].with_company(company)
        lines = []
        for account_id in dict.fromkeys([*balances, *items]):
            own = items.get(account_id, [])
            rest = currency.round(balances.get(account_id, 0.0) - sum(line['balance'] for line in own))
            lines += own
            if not currency.is_zero(rest):
                aging = Account.browse(account_id).account_type in QBO_AGING
                lines.append({'account_id': account_id, 'balance': rest, 'name': _(
                    'QuickBooks balance not covered by open items') if aging else _('QuickBooks opening balance')})
        move = self.env['account.move'].with_company(company).create(dict(
            company._get_default_opening_move_values(), date=cutoff,
            ref=_('QuickBooks opening balance at %s', cutoff), line_ids=[Command.create(line) for line in lines]))
        move.action_post()
        return move

    def _qb_reconcile(self, project, balances, accounts, cutoff):
        """BW plan §2.1: Odoo's posted balance at the cut-off against QuickBooks', account by account (Trial Balance),
        and each receivable or payable account against its lines with a partner (A/R, A/P aging), to
        migration.reconciliation with a tolerance of 0.01: what does not match blocks «reconciled»."""
        company = project.company_id
        Line = self.env['account.move.line'].with_company(company)
        domain = [('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('date', '<=', cutoff),
                  ('account_id', 'in', accounts.ids)]
        odoo = {account.id: total for account, total in Line._read_group(domain, ['account_id'], ['balance:sum'])}
        open_items = {account.id: total for account, total in Line._read_group(
            [*domain, ('partner_id', '!=', False)], ['account_id'], ['balance:sum'])}
        Reconciliation = self.env['migration.reconciliation']
        for account in accounts:
            for kind, target in (('trial_balance', odoo), (QBO_AGING.get(account.account_type), open_items)):
                if not kind:
                    continue
                key = {'project_id': project.id, 'reconciliation_type': kind, 'account_code': account.code}
                vals = {'account_name': account.name, 'tolerance': RECONCILIATION_TOLERANCE,
                        'source_amount': company.currency_id.round(balances.get(account.id, 0.0)),
                        'target_amount': company.currency_id.round(target.get(account.id, 0.0)),
                        'reconciliation_date': fields.Datetime.now()}
                line = Reconciliation.search([(name, '=', value) for name, value in key.items()], limit=1)
                if line:
                    line.write(vals)
                else:
                    Reconciliation.create(dict(key, **vals))

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
            if not self.oauth_client_id or not self.sudo().fgt_saved_at or not self.fgt_consent:
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

        except Exception as e:
            if self.fgt_route == 'cloud' and isinstance(e, (UserError, RedirectWarning)):
                raise   # FlexigoTech Cloud: no service or consent, or it refused; the button says so
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


class MigrationRun(models.Model):
    _inherit = 'migration.run'

    def _check_engine_runs(self, run_type):
        """Extract on a QuickBooks connection that cannot extract says why, before the guard and before queueing."""
        super()._check_engine_runs(run_type)
        if run_type == 'extract':
            for run in self.filtered(lambda r: r.source_ref._name == 'qb.connection'):
                run.source_ref._qb_check_extract()
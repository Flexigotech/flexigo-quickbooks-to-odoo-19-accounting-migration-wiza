# -*- coding: utf-8 -*-
import base64
import csv
import hashlib
import json
import logging
import os
import requests
import time as clock
from datetime import datetime, time, timedelta
from odoo import Command, models, fields, api, _
from odoo.exceptions import AccessError, RedirectWarning, UserError

from ..lib.qbdt_export_parser import IIF_LISTS, decode, iif_records

_logger = logging.getLogger(__name__)

# BW plan §2.2-2.6 (18.0.1.1.0, ported from 19.0.1.1.0): FlexigoTech Cloud keeps the customer's Intuit app and its tokens, one vault entry per
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
    # every credit note: what is left of it cannot be asked for (RemainingCredit is not filterable, VendorCredit.Balance
    # only in QBW: Finance.xsd); the opening skips the ones at 0
    'creditmemo': ('CreditMemo', '', 'CustomerRef', 'DocNumber'),
    'bill': ('Bill', "Balance > '0'", 'VendorRef', 'DocNumber'),
    'vendorcredit': ('VendorCredit', '', 'VendorRef', 'DocNumber'),
}
TRIAL_BALANCE = 'custom'
# BW step 4b-2d: staged as 'custom' too, by its Header.ReportName (the reports of Intuit say it: QuickBooks-V3-DotNET-SDK,
# Test/Intuit.Ipp.Test/SDKV3Test/Services/QBO/Reports.cs), the stock at the cut-off; and the location it comes from.
INVENTORY_REPORT = 'InventoryValuationSummary'
OPENING_STOCK = 'QuickBooks opening stock'
QBO_PAGE = 1000
# A 429 (throttled) or 5xx of Intuit is asked again up to QBO_RETRIES times, waiting what its Retry-After says or 2, 4
# and 8 seconds, never more than QBO_RETRY_WAIT: then the run fails where it was and Extract goes on from that page.
QBO_RETRIES = 3
QBO_RETRY_WAIT = 10
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
    'Other Expense': 'expense',   # 17/18 have no expense_other
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
# its account, the Odoo type of that account, sign, the field of what is left open, its field in the home currency).
# Finance.xsd: Invoice and Bill (Balance, HomeBalance, DueDate, ARAccountRef, APAccountRef); 4b-2c, the credit notes
# with the opposite sign: CreditMemo (RemainingCredit, of a SalesTransaction like Invoice) and VendorCredit (Balance,
# of a PurchaseByVendor: no DueDate). Neither has a home amount of what is left: it goes at its ExchangeRate.
QBO_OPEN_ITEMS = {
    'invoice': ('customer', 'CustomerRef', 'ARAccountRef', 'asset_receivable', 1, 'Balance', 'HomeBalance'),
    'creditmemo': ('customer', 'CustomerRef', 'ARAccountRef', 'asset_receivable', -1, 'RemainingCredit', None),
    'bill': ('vendor', 'VendorRef', 'APAccountRef', 'liability_payable', -1, 'Balance', 'HomeBalance'),
    'vendorcredit': ('vendor', 'VendorRef', 'APAccountRef', 'liability_payable', 1, 'Balance', None),
}
QBO_AGING = {'asset_receivable': 'ar_aging', 'liability_payable': 'ap_aging'}
RECONCILIATION_TOLERANCE = 0.01


def _report_rows(report):
    """The cells of the rows of an Intuit report that name an account or an item (ColData[0].id), those inside its
    sections too: a row is a section (Header, Rows, Summary) or a data row (ColData), Report.xsd Row. A section, its
    summary and the TOTAL name none and are left out."""
    rows = list((report.get('Rows') or {}).get('Row', []))
    for row in rows:   # the rows of a section are added to the list being walked
        rows += (row.get('Rows') or {}).get('Row', [])
    return [row['ColData'] for row in rows if (row.get('ColData') or [{}])[0].get('id')]


def _retry_wait(response, attempt):
    """Seconds before asking again: the Retry-After of the answer (in seconds), else 2, 4, 8…, at most QBO_RETRY_WAIT."""
    try:
        wait = float((response.headers or {}).get('Retry-After'))
    except (TypeError, ValueError):
        wait = 2.0 ** (attempt + 1)
    return min(max(wait, 0.0), QBO_RETRY_WAIT)


def _fernet(env):
    """FR-004: the Fernet keys are MIGRATION_ENCRYPTION_KEY when it is a valid key, and one derived from the database
    secret (only administrators read it). New values use the first; any of them decrypts, so setting or removing the
    variable loses nothing. Before 1.0.1 a missing key made the encryption fail and store the value as it came."""
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
    """FR-004: Decrypt sensitive values. What versions before 1.0.1 stored in plain text is returned as it is until the
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
    # QuickBooks Desktop: the IIF export of its lists, uploaded on a File Upload connection (lib/qbdt_export_parser.py)
    qbdt_file = fields.Binary(string='QuickBooks Desktop export (IIF)', attachment=True, copy=False,
                              help='In QuickBooks Desktop: File > Utilities > Export > Lists to IIF Files.')
    qbdt_filename = fields.Char(string='File name', copy=False)

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
        # Odoo 17/18 do not check the groups of the fields in create() (19 does): an administrator-only field, given or
        # through a default_ key of the context, is refused to anyone else here too
        names = {name for vals in vals_list for name in vals}
        names |= {key[8:] for key in self.env.context if key.startswith('default_')}
        self.check_field_access_rights('write', [name for name in names if name in self._fields])
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
        def send():
            headers = dict(kwargs['headers'], Authorization='Bearer %s' % self._get_oauth_access_token())
            return getattr(requests, method.lower())('%s/%s' % (QBO_HOSTS[bool(self.is_sandbox)], path),
                                                     **dict(kwargs, headers=headers))
        response = send()
        if response.status_code == 401 and self.sudo().oauth_refresh_token_encrypted:
            # Intuit's access token lasts an hour: renew it once with the refresh token and ask again
            self.refresh_access_token()
            response = send()
        return response

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
            if connection.connection_type == 'upload':
                connection._qbdt_records()   # says why the file cannot be read
                continue
            if connection.fgt_route != 'cloud':
                raise UserError(_('This QuickBooks connection was made before FlexigoTech Cloud: it keeps working as '
                                  'bought (Connect to QuickBooks and Test Connection), but the extraction goes through '
                                  'FlexigoTech Cloud. To extract, create a new QuickBooks connection, which goes '
                                  'through FlexigoTech Cloud, and connect it with Intuit: it takes two minutes.'))
            if not connection.realm_id:
                raise UserError(_('Connect this QuickBooks connection with Intuit first (Connect to QuickBooks).'))

    def _migration_object_types(self):
        if self.connection_type == 'upload':
            return list(IIF_LISTS.values())
        return [*QBO_ENTITIES, TRIAL_BALANCE]

    def _qbdt_records(self):
        """The lists of the uploaded QuickBooks Desktop export as QuickBooks Online payloads, and the other lists of the
        file (not read); a UserError says why the file cannot be read."""
        self.ensure_one()
        if not self.qbdt_file:
            raise UserError(_('Upload the IIF export of QuickBooks Desktop on this connection first (in QuickBooks '
                              'Desktop: File > Utilities > Export > Lists to IIF Files).'))
        try:
            records, other = iif_records(decode(base64.b64decode(self.qbdt_file)))
        except csv.Error as error:   # a picture, an archive or a PDF read as text
            raise UserError(_('%(file)s cannot be read as an IIF export of QuickBooks Desktop (%(error)s): upload the '
                              'file QuickBooks Desktop writes in File > Utilities > Export > Lists to IIF Files.',
                              file=self.qbdt_filename or _('The file'), error=error)) from error
        if not any(records.values()):
            raise UserError(_('%(file)s has no account, class, customer, vendor or item list of an IIF export of '
                              'QuickBooks Desktop (File > Utilities > Export > Lists to IIF Files).',
                              file=self.qbdt_filename or _('The file')))
        return records, other

    def _migration_extract_page(self, object_type, cursor):
        """One page for the engine: the query of the entity from STARTPOSITION cursor (1 the first time) until a page
        comes short, or the reports to today: the Trial Balance and, at the same date, the Inventory Valuation Summary.
        A refusal of FlexigoTech Cloud goes up as it is: the engine pauses."""
        self.ensure_one()
        self._qb_check_extract()
        start = cursor or 1
        if self.connection_type == 'upload':
            rows = self._qbdt_records()[0][object_type][start - 1:start - 1 + QBO_PAGE]
            return rows, (start + QBO_PAGE if len(rows) == QBO_PAGE else None)
        if object_type == TRIAL_BALANCE:
            day = fields.Date.to_string(fields.Date.context_today(self))
            # the date of each report has its own name: end_date, and report_date for the inventory (Reports.cs of
            # QuickBooks-V3-DotNET-SDK l.202-206 and l.248-255; ReportService.cs l.219)
            return [self._qbo_get('reports/TrialBalance', {'end_date': day}),
                    self._qbo_get('reports/' + INVENTORY_REPORT, {'report_date': day})], None
        entity, where, _name, _code = QBO_ENTITIES[object_type]
        query = 'SELECT * FROM %s%s STARTPOSITION %d MAXRESULTS %d' % (
            entity, where and ' WHERE ' + where, start, QBO_PAGE)
        # Intuit leaves the entity out of QueryResponse when there are no rows
        rows = self._qbo_get('query', {'query': query}).get('QueryResponse', {}).get(entity, [])
        return rows, (start + QBO_PAGE if len(rows) == QBO_PAGE else None)

    def _qbo_get(self, path, params):
        """GET v3/company/<realm>/<path> with the minor version of the connection: Intuit's JSON, or UserError. A 429 or
        5xx is asked again (QBO_RETRIES) before it fails."""
        for attempt in range(QBO_RETRIES + 1):
            response = self._qbo_send('GET', 'v3/company/%s/%s' % (self.realm_id, path),
                                      params=dict(params, minorversion=self.minorversion or '75'))
            if attempt == QBO_RETRIES or response.status_code != 429 and response.status_code < 500:
                break
            clock.sleep(_retry_wait(response, attempt))
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
            header = payload.get('Header') or {}
            report = header.get('ReportName') or 'TrialBalance'
            return {'source_id': report, 'source_realm': self.realm_id, 'normalized_name': report,
                    'normalized_code': header.get('EndPeriod') or False, 'normalized_active': True}
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
        invoices, bills and credit notes are lines of the opening entry the Trial Balance makes. The other report is the
        stock at the cut-off. A UserError tells the user what to map."""
        if staging.object_type == TRIAL_BALANCE and staging.source_id == INVENTORY_REPORT:
            return self._qb_load_stock(staging, staging.raw_payload or {})
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
        link = Link.search([(name, '=', value) for name, value in key.items()], limit=1)
        if not link:
            Link.create(dict(key, res_model=record._name, res_id=record.id))
        elif (link.res_model, link.res_id) != (record._name, record.id):
            # the user mapped it to another record of Odoo (or the one of a load was deleted): the link follows the mapping
            link.write({'res_model': record._name, 'res_id': record.id})
        return record._name, {}

    def _qb_posted_elsewhere(self, staging):
        """Another project of the same company already posted this entry (the opening or the stock at the cutoff):
        one per company, whatever the project."""
        project = staging.project_id
        for link in self.env['migration.record.link'].search([
                ('object_type', '=', staging.object_type), ('source_id', '=', staging.source_id),
                ('project_id', '!=', project.id), ('project_id.company_id', '=', project.company_id.id)]):
            if self.env[link.res_model].browse(link.res_id).exists():
                raise UserError(_('%(company)s already has this QuickBooks entry, posted by the migration project '
                                  '%(project)s: it is made once per company.', company=project.company_id.name,
                                  project=link.project_id.name))

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
                                   # Odoo 17/18: an account is deprecated, not archived (active came in 19)
                                   'deprecated': not staging.normalized_active}

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
        if kind['is_storable'] and self._qb_linked(staging.project_id, 'item', staging.source_id):
            # its cost values its stock (quantity × cost) since the stock load set QuickBooks' at the cut-off: loading
            # the items again does not revalue it
            del vals['standard_price']
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
            self._qb_posted_elsewhere(staging)
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
        for cells in _report_rows(report):
            account = self._qb_linked(project, 'account', cells[0]['id'])
            amount = float(cells[debit].get('value') or 0) - float(cells[credit].get('value') or 0)
            if not account:
                missing.append(cells[0].get('value') or cells[0]['id'])
            elif amount:
                balances[account.id] = balances.get(account.id, 0.0) + amount
        if missing:
            raise UserError(_('These accounts of the Trial Balance are not loaded, or were deleted in Odoo after a load, so '
                              'there is no opening entry: %s. Map them to an account of the chart in the mapping set of '
                              'the project and load again.', ', '.join(missing)))
        return balances, fields.Date.to_date(header['EndPeriod'])

    def _qb_open_items(self, project, balances):
        """{account id: [lines]} of the open invoices, bills and credit notes staged: its partner by its link,
        DocNumber as label (the ref of a line is its entry's in Odoo), DueDate, and what is left open (QBO_OPEN_ITEMS);
        in another currency, that in it and its home field (HomeBalance) or, with none, at its ExchangeRate in the
        company's. The account is the one the item names, else the only receivable or payable one of the Trial
        Balance. What cannot be placed is said, not guessed."""
        company = project.company_id
        currency = company.currency_id
        in_balance = self.env['account.account'].with_company(company).browse(list(balances))
        items, problems = {}, []
        for staging in self.env['migration.staging.record'].search([
                ('project_id', '=', project.id), ('object_type', 'in', list(QBO_OPEN_ITEMS)),
                ('validation_status', '!=', 'invalid')], order='object_type, id'):
            partner_type, partner_ref, account_ref, account_type, sign, open_field, home_field = \
                QBO_OPEN_ITEMS[staging.object_type]
            row = staging.raw_payload or {}
            amount = row.get(open_field)
            if not amount:
                continue
            label = row.get('DocNumber') or '%s %s' % (QBO_ENTITIES[staging.object_type][0], staging.source_id)
            partner = self._qb_linked(project, partner_type, (row.get(partner_ref) or {}).get('value'))
            account = self._qb_linked(project, 'account', (row.get(account_ref) or {}).get('value'))
            if not account:
                account = in_balance.filtered(lambda a: a.account_type == account_type)
            code = (row.get('CurrencyRef') or {}).get('value') or currency.name
            foreign = code != currency.name and self.env['res.currency'].search([('name', '=', code)], limit=1)
            if code == currency.name:
                home = amount
            elif home_field:
                home = row.get(home_field)
            else:   # Finance.xsd: ExchangeRate = home currency units for one unit of the CurrencyRef
                home = float(amount) * float(row['ExchangeRate']) if row.get('ExchangeRate') else None
            if not partner or len(account) != 1 or code != currency.name and not (foreign and home is not None):
                problems.append(label)
                continue
            line = {'account_id': account.id, 'partner_id': partner.id, 'name': label,
                    'date_maturity': row.get('DueDate') or False, 'balance': currency.round(sign * float(home))}
            if foreign:
                line.update(currency_id=foreign.id, amount_currency=foreign.round(sign * float(amount)))
            items.setdefault(account.id, []).append(line)
        if problems:
            raise UserError(_('These open invoices, bills or credit notes cannot go into the opening entry (their '
                              'customer or vendor is not loaded, the Trial Balance has no single receivable or payable '
                              'account for them, or their currency is not active in Odoo or has no HomeBalance or '
                              'ExchangeRate): %s.', ', '.join(problems)))
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
        for account in accounts:
            for kind, target in (('trial_balance', odoo), (QBO_AGING.get(account.account_type), open_items)):
                if kind:
                    self._qb_tie_out(project, kind, account.code, account.name, balances.get(account.id, 0.0),
                                     target.get(account.id, 0.0))

    def _qb_tie_out(self, project, kind, code, name, source, target):
        """One line of migration.reconciliation, made or updated by project, type and code, with a tolerance of 0.01."""
        currency = project.company_id.currency_id
        key = {'project_id': project.id, 'reconciliation_type': kind, 'account_code': code}
        vals = {'account_name': name, 'tolerance': RECONCILIATION_TOLERANCE, 'source_amount': currency.round(source),
                'target_amount': currency.round(target), 'reconciliation_date': fields.Datetime.now()}
        Reconciliation = self.env['migration.reconciliation']
        line = Reconciliation.search([(field, '=', value) for field, value in key.items()], limit=1)
        if line:
            line.write(vals)
        else:
            Reconciliation.create(dict(key, **vals))

    def _qb_load_stock(self, staging, report):
        """BW plan §2.1, the stock at the cut-off: the quantity and asset value of each inventory item in Intuit's
        Inventory Valuation Summary go into the main warehouse of the company, once (its link), with no stock valuation
        entry; and every time it is loaded Odoo's value is reconciled with QuickBooks', item by item. No stock, nothing."""
        project = staging.project_id
        stock, cutoff = self._qb_inventory(project, report)
        if not stock:
            return None
        if not self.env['migration.record.link'].search_count([
                ('project_id', '=', project.id), ('object_type', '=', staging.object_type),
                ('source_id', '=', staging.source_id)]):
            self._qb_posted_elsewhere(staging)
            self._qb_existing(staging, self._qb_stock_moves(project.company_id, stock, cutoff))
        for product, (_quantity, value) in stock.items():
            self._qb_tie_out(project, 'inventory', product.default_code or product.name, product.name, value,
                             product.value_svl)
        return 'stock.location', {}

    def _qb_inventory(self, project, report):
        """{product: (quantity, value)} of Intuit's Inventory Valuation Summary, its rows by the link of their item
        (in sections too: Report.xsd), and its date, else the one of the Trial Balance asked with it. In the home
        currency, which must be the company's. An item that is not a loaded storable product, or below zero, is said
        and nothing is loaded."""
        company = project.company_id
        header = report.get('Header') or {}
        if header.get('Currency', company.currency_id.name) != company.currency_id.name:
            raise UserError(_('The Inventory Valuation Summary of QuickBooks is in %(source)s and %(company)s works in '
                              '%(target)s.', source=header['Currency'], company=company.name,
                              target=company.currency_id.name))
        titles = [column.get('ColTitle') for column in (report.get('Columns') or {}).get('Column', [])]
        if not {'Qty', 'Asset Value'} <= set(titles):
            raise UserError(_('The Inventory Valuation Summary of QuickBooks has no Qty and Asset Value columns.'))
        quantity_at, value_at = titles.index('Qty'), titles.index('Asset Value')
        stock, problems = {}, []
        for cells in _report_rows(report):
            quantity, value = (float(cells[at].get('value') or 0) for at in (quantity_at, value_at))
            if not quantity and not value:
                continue
            template = self._qb_linked(project, 'item', cells[0]['id'])
            if not template or not template.is_storable or not template.product_variant_id or quantity <= 0:
                problems.append(cells[0].get('value') or cells[0]['id'])
            else:
                stock[template.product_variant_id.with_company(company)] = (quantity, value)
        if problems:
            raise UserError(_('These items of the Inventory Valuation Summary are not loaded as storable products, or '
                              'their quantity is below zero, so no stock is loaded: %s. Load them, or fix their '
                              'quantity in QuickBooks, and load again.', ', '.join(problems)))
        if not stock:
            return stock, None
        cutoff = header.get('EndPeriod') or self.env['migration.staging.record'].search([
            ('project_id', '=', project.id), ('object_type', '=', TRIAL_BALANCE), ('source_id', '=', 'TrialBalance')],
            limit=1).normalized_code
        if not cutoff:
            raise UserError(_('The Inventory Valuation Summary of QuickBooks has no date.'))
        return stock, cutoff

    def _qb_stock_moves(self, company, stock, cutoff):
        """The stock into the main warehouse of the company at the end of the cut-off day, from a location of its own
        whose moves post no stock valuation entry (StockMove._account_entry_move below): the opening entry already
        brought the inventory account, in perpetual (real_time) valuation too. Each product is valued at QuickBooks'
        asset value: its cost is the value over the quantity (what standard and average cost value the stock at) and
        the valuation layer of its move has that value, to the cent."""
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        if not warehouse:
            raise UserError(_('%s has no warehouse to put the stock of QuickBooks in.', company.name))
        Location = self.env['stock.location'].with_company(company)
        source = Location.search([('name', '=', OPENING_STOCK), ('usage', '=', 'inventory'),
                                  ('company_id', '=', company.id)], limit=1) \
            or Location.create({'name': OPENING_STOCK, 'usage': 'inventory', 'company_id': company.id})
        # the last second of the cut-off day in UTC and in the company's time zone
        day_end = datetime.combine(fields.Date.to_date(cutoff), time(23, 59, 59))
        offset = fields.Datetime.context_timestamp(company.with_context(tz=company.partner_id.tz or 'UTC'),
                                                   day_end).utcoffset()
        moment = min(day_end, day_end - offset)
        for product, (quantity, value) in stock.items():
            product.standard_price = value / quantity
        label = _('QuickBooks stock at %s', cutoff)
        moves = self.env['stock.move'].with_company(company).create([{
            'name': label, 'product_id': product.id, 'product_uom': product.uom_id.id, 'product_uom_qty': quantity,
            'price_unit': value / quantity, 'location_id': source.id, 'location_dest_id': warehouse.lot_stock_id.id,
            'company_id': company.id, 'origin': label} for product, (quantity, value) in stock.items()])
        moves = moves._action_confirm()
        moves._action_assign()
        moves.picked = True
        moves = moves._action_done()
        currency = company.currency_id
        for move in moves:
            quantity, value = stock[move.product_id]
            for layer in move.sudo().stock_valuation_layer_ids:
                if currency.compare_amounts(layer.value, value):   # the cost rounded to its digits, times the quantity
                    layer.write(dict({'value': value, 'unit_cost': value / quantity},
                                     **({'remaining_value': value} if layer.remaining_qty else {})))
        moves.write({'date': moment})
        moves.move_line_ids.write({'date': moment})   # 17/18: the date of a move does not go to its lines
        return source

    def _encrypt_stored_secrets(self):
        """1.0.1 upgrade: encrypt what older versions stored in plain text (a token of the key is left alone)."""
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
            elif response.status_code == 401:
                self.is_validated = False
                self.validation_message = _('Connection failed: QuickBooks refused the access of this connection '
                                            '(HTTP 401). Connect it again with Connect to QuickBooks Online.')
            else:
                self.is_validated = False
                self.validation_message = f"Connection failed: HTTP {response.status_code}"

        except Exception as e:
            if self.fgt_route == 'cloud' and isinstance(e, (UserError, RedirectWarning)):
                raise   # FlexigoTech Cloud: no service or consent, or it refused; the button says so
            self.is_validated = False
            # FR-004: Never log secrets (our own messages carry none)
            self.validation_message = "Connection failed: %s" % (
                e.args[0] if isinstance(e, UserError) else str(e)[:100])
        
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
        """The uploaded QuickBooks Desktop export: what Extract reads from it, or why it cannot be read."""
        try:
            records, other = self._qbdt_records()
            labels = {'account': _('accounts'), 'class': _('classes'), 'customer': _('customers'),
                      'vendor': _('vendors'), 'item': _('items')}
            self.is_validated = True
            self.validation_message = _('Extract reads %s.', ', '.join(
                '%d %s' % (len(rows), labels[object_type]) for object_type, rows in records.items()))
            if other:
                self.validation_message += ' ' + _('Not read: %s.', ', '.join(
                    '%s (%d)' % (name, count) for name, count in sorted(other.items())))
        except UserError as error:
            self.is_validated = False
            self.validation_message = error.args[0]
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Upload Validation'),
                'message': self.validation_message,
                'type': 'success' if self.is_validated else 'danger',
            }
        }

    # FR-002: Refresh token handling with rotation
    @api.private
    def refresh_access_token(self):
        """FR-002: Refresh OAuth access token, handling refresh token rotation"""
        self._check_can_manage()
        if self.fgt_route == 'cloud':
            return True   # FlexigoTech Cloud renews the access by itself: no token is kept here
        if not self.sudo().oauth_refresh_token_encrypted:
            raise UserError(_("No refresh token available"))

        # FR-002: Atomic update of both tokens. Intuit's token endpoint, grant_type refresh_token: it answers a new
        # access token (expires_in) and a refresh token (x_refresh_token_expires_in) that replaces the one sent
        reconnect = _('Connect it again with Connect to QuickBooks Online.')
        try:
            response = self._qbo_send('POST', INTUIT_TOKEN_URL, data={
                'grant_type': 'refresh_token', 'refresh_token': self._get_oauth_refresh_token()})
        except requests.RequestException as e:
            raise UserError(_('Odoo could not reach QuickBooks to renew the access (%s). Try again in a moment.',
                              type(e).__name__)) from None
        try:
            data = response.json() if response.status_code == 200 else {}
            vals = {'oauth_access_token_encrypted': data['access_token'],
                    'oauth_refresh_token_encrypted': data['refresh_token']}
            if data.get('x_refresh_token_expires_in'):
                vals['oauth_refresh_token_expiry'] = fields.Datetime.now() + timedelta(
                    seconds=int(data['x_refresh_token_expires_in']))
        except (ValueError, KeyError, TypeError, OverflowError):
            raise UserError(_('QuickBooks did not renew the access of this connection (HTTP %(status)s): its '
                              'authorization ended or was revoked. %(reconnect)s', status=response.status_code,
                              reconnect=reconnect)) from None
        self.sudo().write(vals)   # write() encrypts the tokens
        return True


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _account_entry_move(self, qty, description, svl_id, cost):
        """The QuickBooks stock at the cut-off comes from its own opening location and posts no stock valuation entry:
        the opening entry already brought the inventory account (19.0 gets the same from a location with no valuation
        account, which 17/18 do not have)."""
        if self.location_id.usage == 'inventory' and self.location_id.name == OPENING_STOCK:
            return []
        return super()._account_entry_move(qty, description, svl_id, cost)


class MigrationRun(models.Model):
    _inherit = 'migration.run'

    def _check_engine_runs(self, run_type):
        """Extract on a QuickBooks connection that cannot extract says why, before the guard and before queueing."""
        super()._check_engine_runs(run_type)
        if run_type == 'extract':
            for run in self.filtered(lambda r: r.source_ref._name == 'qb.connection'):
                run.source_ref._qb_check_extract()

class MigrationProject(models.Model):
    _inherit = 'migration.project'

    def action_qb_delete_staging(self):
        """Delete the staging of a loaded project (button, Migration Managers): the records as QuickBooks gave them, with
        the names, emails and phones of customers and vendors. What was loaded into Odoo stays, and so do the links
        that keep loading again from creating anything twice. Extract fills the staging again."""
        if not self.env.user.has_group('flexigo_migration_base.group_migration_manager'):
            raise AccessError(_('Only members of the Migration Manager group delete the staging of a project.'))
        self.check_access('read')
        for project in self:
            if project.status not in ('loaded', 'reconciled', 'live'):
                raise UserError(_('Delete the staging of %s once it is loaded: Load reads it.', project.name))
            if self.env['migration.run'].search_count([('project_id', '=', project.id),
                                                       ('state', 'in', ('queued', 'running', 'paused'))]):
                raise UserError(_('A run of %s is still going on: wait until it ends.', project.name))
        self.env['migration.staging.record'].sudo().search([('project_id', 'in', self.ids)]).unlink()
        return True


class MigrationReconciliation(models.Model):
    _inherit = 'migration.reconciliation'

    def action_qb_sign_off(self):
        """Sign off reconciliation lines (button): members of QuickBooks Migration / Reviewer, with their name and the
        time; the other internal users only read them."""
        if not self.env.user.has_group(
                'flexigo_quickbooks_to_odoo_19_accounting_migration_wiza.group_migration_reviewer'):
            raise AccessError(_('Only members of QuickBooks Migration / Reviewer sign off the reconciliation.'))
        self.check_access('read')
        self.sudo().write({'signed_off': True, 'signed_off_by': self.env.uid,
                           'signed_off_date': fields.Datetime.now()})
        return True

    _QB_SIGN_OFF_FIELDS = {'signed_off', 'signed_off_by', 'signed_off_date'}

    def _qb_check_sign_off_vals(self, vals_list, new=False):
        # only Sign Off signs, as superuser after checking the reviewer: nobody signs in a reviewer's name (R-k 524);
        # a new line may say it is not signed (the form sends the default)
        if not self.env.su and any(name in vals and (vals[name] or not new)
                                   for vals in vals_list for name in self._QB_SIGN_OFF_FIELDS):
            raise AccessError(_('Only Sign Off, by a member of QuickBooks Migration / Reviewer, signs off the '
                                'reconciliation.'))

    @api.model_create_multi
    def create(self, vals_list):
        self._qb_check_sign_off_vals(vals_list, new=True)
        return super().create(vals_list)

    def write(self, vals):
        self._qb_check_sign_off_vals([vals])
        return super().write(vals)


class MigrationStagingRecord(models.Model):
    _inherit = 'migration.staging.record'

    def unlink(self):
        # the staging goes with Delete Staging of a loaded project (its checks, then as superuser), not row by row;
        # qb.staging (its own table, Cola 259) inherits this class and keeps its delete
        if self._name == 'migration.staging.record' and not self.env.su:
            raise AccessError(_('Delete the staging with Delete Staging on the migration project, once it is loaded.'))
        return super().unlink()


class MigrationRecordLink(models.Model):
    _inherit = 'migration.record.link'

    def unlink(self):
        # a link says what was loaded: without it, the next load creates that record again
        if not self.env.su:
            raise AccessError(_('A migration link says what was loaded into Odoo: without it, the next load would '
                                'create that record twice.'))
        return super().unlink()

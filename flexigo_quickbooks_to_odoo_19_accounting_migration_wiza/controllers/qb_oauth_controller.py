# -*- coding: utf-8 -*-
import logging
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import requests

from odoo import fields, http
from odoo.http import request
from odoo.tools import consteq

_logger = logging.getLogger(__name__)

# Intuit's discovery documents (production and sandbox): the same endpoints for both; sandbox is decided by the app keys.
INTUIT_AUTHORIZE_URL = 'https://appcenter.intuit.com/connect/oauth2'
INTUIT_TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'
MODULE = 'flexigo_quickbooks_to_odoo_19_accounting_migration_wiza'


class QuickBooksOAuthController(http.Controller):
    """FR-002: OAuth 2.0 authorization-code grant with QuickBooks Online (Intuit)."""

    def _error(self, error, description):
        _logger.warning("QuickBooks OAuth: %s", error)
        return request.render('%s.qb_oauth_error' % MODULE, {'error': error, 'error_description': description},
                              status=400)

    @http.route('/qb/oauth/authorize', type='http', auth='user')
    def initiate_oauth(self, connection_id=None, **kw):
        """Send the user to Intuit with a fresh state, kept in the session for the callback."""
        if not str(connection_id or '').isdigit():
            return request.not_found()
        connection = request.env['qb.connection'].browse(int(connection_id)).exists()
        if not connection:
            return request.not_found()
        connection._check_can_manage()
        state = secrets.token_urlsafe(32)
        request.session['qb_oauth'] = {'connection_id': connection.id, 'state': state}
        params = {
            'client_id': connection.oauth_client_id,
            'response_type': 'code',
            'scope': 'com.intuit.quickbooks.accounting',
            'redirect_uri': connection._get_oauth_redirect_uri(),
            'state': state,
        }
        return request.redirect('%s?%s' % (INTUIT_AUTHORIZE_URL, urlencode(params)), local=False)

    @http.route('/qb/oauth/callback', type='http', auth='user', csrf=False)
    def oauth_callback(self, code=None, state=None, error=None, **kw):
        """Intuit comes back with code, state and realmId: exchange the code and store the tokens (encrypted by write())."""
        pending = request.session.pop('qb_oauth', None) or {}
        if error:
            return self._error(error, kw.get('error_description') or 'QuickBooks did not authorize the connection.')
        if not state or not consteq(str(pending.get('state', '')), state):
            return self._error('invalid_state', 'This QuickBooks authorization was not started from this Odoo session. '
                                                'Start it again from the connection.')
        realm_id = kw.get('realmId')
        if not code or not realm_id:
            return self._error('invalid_request', 'QuickBooks did not send the authorization code or the company (realmId).')
        connection = request.env['qb.connection'].browse(pending.get('connection_id')).exists()
        if not connection:
            return self._error('connection_not_found', 'The QuickBooks connection of this authorization no longer exists.')
        connection._check_can_manage()  # the client secret goes to Intuit below
        try:
            response = requests.post(
                INTUIT_TOKEN_URL,
                auth=(connection.oauth_client_id, connection._get_oauth_client_secret()),
                data={'grant_type': 'authorization_code', 'code': code,
                      'redirect_uri': connection._get_oauth_redirect_uri()},
                headers={'Accept': 'application/json'},
                timeout=30,
            )
        except requests.RequestException as exc:
            _logger.warning("QuickBooks OAuth: token exchange failed: %s", type(exc).__name__)
            return self._error('token_exchange_failed', 'Odoo could not reach QuickBooks. Try again in a moment.')
        if response.status_code != 200:
            try:
                description = response.json().get('error_description') or response.json().get('error')
            except ValueError:
                description = None
            return self._error('token_exchange_failed', description or 'QuickBooks refused the authorization code (HTTP %s).'
                               % response.status_code)
        token_data = response.json()
        connection.write({'realm_id': realm_id, 'is_validated': False})
        values = {
            'oauth_access_token_encrypted': token_data['access_token'],
            'oauth_refresh_token_encrypted': token_data['refresh_token'],
        }
        if token_data.get('x_refresh_token_expires_in'):
            values['oauth_refresh_token_expiry'] = fields.Datetime.now() + timedelta(
                seconds=int(token_data['x_refresh_token_expires_in']))
        connection.sudo().write(values)  # admin-only fields, encrypted by the model's write()
        _logger.info("QuickBooks OAuth: tokens stored for connection %s", connection.id)
        return request.redirect('/web#id=%d&model=qb.connection&view_type=form' % connection.id)

# -*- coding: utf-8 -*-
import json
import logging
import requests
from urllib.parse import urlencode
from odoo import http
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class QuickBooksOAuthController(http.Controller):
    """FR-002: OAuth 2.0 callback endpoint for QuickBooks Online authorization-code grant"""

    @http.route('/qb/oauth/callback', type='http', auth='user', csrf=False)
    def oauth_callback(self, code=None, realm_id=None, state=None, error=None, **kw):
        """
        FR-002: Handle OAuth 2.0 authorization code callback from Intuit

        The OAuth flow:
        1. User clicks "Connect QuickBooks Online"
        2. Redirects to Intuit authorization endpoint with client_id, redirect_uri, scope
        3. User grants permission
        4. Intuit redirects back to this endpoint with authorization code
        5. We exchange the code for access/refresh tokens
        6. We store the tokens (encrypted) in the connection profile
        """

        if error:
            _logger.error(f"OAuth error from Intuit: {error}")
            return http.request.render('qb_oauth_error.html', {
                'error': error,
                'error_description': kw.get('error_description', 'Authorization failed'),
            })

        if not code or not realm_id:
            _logger.error("Missing OAuth code or realmId in callback")
            return http.request.render('qb_oauth_error.html', {
                'error': 'invalid_request',
                'error_description': 'Missing authorization code or realm ID',
            })

        # FR-002: Exchange authorization code for access token
        try:
            # Get the connection profile from session or request
            connection_id = http.request.session.get('qb_connection_id')
            if not connection_id:
                # Try to find the connection by realm_id (for user-initiated flows)
                connection = http.request.env['qb.connection'].search([
                    ('realm_id', '=', realm_id),
                ], limit=1)
                if connection:
                    connection_id = connection.id

            if not connection_id:
                return http.request.render('qb_oauth_error.html', {
                    'error': 'connection_not_found',
                    'error_description': 'Could not find the connection profile for this OAuth flow',
                })

            connection = http.request.env['qb.connection'].browse(connection_id)

            # Exchange code for tokens
            token_url = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'

            response = requests.post(
                token_url,
                auth=(connection.oauth_client_id, connection._get_oauth_client_secret()),
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'realm_id': realm_id,
                },
                headers={'Accept': 'application/json'},
                timeout=30
            )

            if response.status_code == 200:
                token_data = response.json()

                # FR-004: Store tokens encrypted (never in plaintext)
                # Note: tokens are encrypted by the qb.connection model before storage
                # This controller just handles the OAuth callback and passes tokens to the model

                connection.write({
                    'oauth_access_token_encrypted': token_data['access_token'],  # Will be encrypted by model
                    'oauth_refresh_token_encrypted': token_data['refresh_token'],  # Will be encrypted by model
                    'realm_id': realm_id,
                    'is_validated': False,  # Will be set to True after test_connection
                })

                _logger.info(f"OAuth tokens successfully obtained for realm {realm_id}")

                # Redirect to the connection form
                return http.redirect(f'/web#id={connection_id}&model=qb.connection&view_type=form')
            else:
                error_msg = response.text
                try:
                    error_data = response.json()
                    error_msg = error_data.get('error_description', error_msg)
                except:
                    pass

                _logger.error(f"Token exchange failed: {error_msg}")
                return http.request.render('qb_oauth_error.html', {
                    'error': 'token_exchange_failed',
                    'error_description': error_msg,
                })

        except Exception as e:
            _logger.exception(f"OAuth callback error: {str(e)}")
            return http.request.render('qb_oauth_error.html', {
                'error': 'server_error',
                'error_description': f'Server error during OAuth processing: {str(e)[:200]}',
            })

    @http.route('/qb/oauth/authorize', type='http', auth='user')
    def initiate_oauth(self, connection_id=None, **kw):
        """
        FR-002: Initiate OAuth 2.0 authorization flow

        Redirects the user to the Intuit authorization endpoint
        """

        if not connection_id:
            return http.request.not_found()

        connection = http.request.env['qb.connection'].browse(int(connection_id))

        # Store connection_id in session for the callback to retrieve
        http.request.session['qb_connection_id'] = connection_id

        # Build the authorization URL
        base_url = 'https://appcenter.intuit.com' if not connection.is_sandbox else 'https://sandbox.intuit.com'

        auth_url = f"{base_url}/connect/oauth2"

        params = {
            'client_id': connection.oauth_client_id,
            'response_type': 'code',
            'scope': 'com.intuit.quickbooks.accounting',
            'redirect_uri': http.request.root_url.rstrip('/') + '/qb/oauth/callback',
            'state': 'state_param_for_security',  # Should be a random value in production
        }

        redirect_url = f"{auth_url}?{urlencode(params)}"

        return http.redirect(redirect_url)

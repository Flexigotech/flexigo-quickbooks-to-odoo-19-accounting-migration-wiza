# Changelog — QuickBooks to Odoo Migration Wizard

## 18.0.1.0.2 (2026-10-23)
- QuickBooks Online can be connected from Odoo. The connection form has the Client ID and Secret, the Redirect URI
  to register in Intuit and a "Connect to QuickBooks Online" button. Before, the form had no such fields, the
  authorization and its return failed with an error, the return looked for the company under the wrong name and
  the code exchange did not send the redirect URI.
- The authorization uses a random state checked on return, and errors show a page that says what went wrong.
- Sandbox connections go to the QuickBooks Online sandbox (they went to hosts that are not QuickBooks Online).
- Only members of the Migration Manager group manage connections; an administrator needs that group too.

## 18.0.1.0.1 (2026-10-23)
- Security: the QuickBooks OAuth client secret and tokens are really encrypted now. Without the
  MIGRATION_ENCRYPTION_KEY environment variable the key used to be a fixed text that is not a valid Fernet key, so
  the encryption failed and the value was stored as it came; the OAuth callback also stored the tokens as they came.
  The key is now MIGRATION_ENCRYPTION_KEY when it is a valid Fernet key, otherwise one derived from the database
  secret, and the upgrade encrypts what was stored in plain text.
- Security: only the administrator reads those fields, and only Migration Managers change, delete or use a QuickBooks
  connection: refreshing the token, testing the connection and the OAuth callback (until now every internal user
  could). Both keys decrypt, so setting MIGRATION_ENCRYPTION_KEY later loses nothing; credentials that no key opens
  (a duplicated database, a changed variable) raise an error asking to enter them again instead of being sent.

## 18.0.1.0.0 (2026-08-11)
- Maintained release: quality, compatibility and store-listing updates.
- Validated on Odoo.sh (install, CRUD and smoke tests).

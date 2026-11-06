# Changelog — QuickBooks to Odoo Migration Wizard

## 19.0.1.1.0 (unreleased)
- New QuickBooks Online connections go through FlexigoTech Cloud, which needs its trial or subscription (Settings >
  FlexigoTech Cloud): the client ID and secret of your Intuit app go to its vault with your consent and never stay in
  Odoo, every call to QuickBooks passes through it, and the authorization code is exchanged there, so no QuickBooks
  token reaches Odoo. Without the service nothing is sent and nothing in Odoo is deleted.
- Connections made before 1.1.0 keep working exactly as bought ("Direct"): the OAuth from Odoo, "Test Connection" and
  their tokens encrypted in Odoo. A connection's route never changes, and only an administrator deletes a direct one.
- Upgrading: put `flexigotech_cloud` in the same addons path, restart and update the module (Apps > Upgrade, or
  `-u flexigo_quickbooks_to_odoo_19_accounting_migration_wiza`); restarting without updating does not load it.
- A copy of the database (neutralized) forgets the QuickBooks tokens; uninstalling deletes the Intuit apps kept in
  FlexigoTech Cloud. New connections default to QuickBooks API minor version 75.

## 19.0.1.0.5 (2026-11-05)
- Each QuickBooks connection and staging row stays in its company: a Migration Manager of another company no longer
  sees a connection, starts its authorization or writes its company and tokens (there were no company rules).
- An odd refresh-token lifetime from QuickBooks shows the error page instead of a server error.
- "Test Connection" only shows on QuickBooks Online connections.
- A Migration Manager who is not an administrator is told to ask one for the Client Secret, which only administrators see.

## 19.0.1.0.4 (2026-10-23)
- QuickBooks Online can be connected from Odoo. The connection form has the Client ID and Secret, the Redirect URI
  to register in Intuit and a "Connect to QuickBooks Online" button. Before, the form had no such fields, the
  authorization and its return failed with an error, the return looked for the company under the wrong name and
  the code exchange did not send the redirect URI.
- The authorization uses a random state checked on return, and errors show a page that says what went wrong.
- Sandbox connections go to the QuickBooks Online sandbox (they went to hosts that are not QuickBooks Online).
- Only members of the Migration Manager group manage connections; an administrator needs that group too.

## 19.0.1.0.3 (2026-10-23)
- Security: the QuickBooks OAuth client secret and tokens are really encrypted now. Without the
  MIGRATION_ENCRYPTION_KEY environment variable the key used to be a fixed text that is not a valid Fernet key, so
  the encryption failed and the value was stored as it came; the OAuth callback also stored the tokens as they came.
  The key is now MIGRATION_ENCRYPTION_KEY when it is a valid Fernet key, otherwise one derived from the database
  secret, and the upgrade encrypts what was stored in plain text.
- Security: only the administrator reads those fields, and only Migration Managers change, delete or use a QuickBooks
  connection: refreshing the token, testing the connection and the OAuth callback (until now every internal user
  could). Both keys decrypt, so setting MIGRATION_ENCRYPTION_KEY later loses nothing; credentials that no key opens
  (a duplicated database, a changed variable) raise an error asking to enter them again instead of being sent.

## 19.0.1.0.2 (2026-09-15)
- Maintained release: quality, compatibility and store-listing updates.
- Validated on Odoo.sh (install, CRUD and smoke tests).

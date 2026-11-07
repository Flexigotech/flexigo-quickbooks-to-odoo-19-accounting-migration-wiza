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
- Extraction into the migration staging, page by page through FlexigoTech Cloud, by the engine of the Migration Base
  (Extract on a migration run): accounts, tax codes, terms, classes, departments, customers, vendors, items, open
  invoices and open bills, and the Trial Balance to the day of the extraction. If FlexigoTech Cloud stops answering or
  the service ends, the run pauses where it was and goes on by itself; the staging is never deleted. A connection made
  before 1.1.0 says that extracting needs a new connection through FlexigoTech Cloud. The Intuit app and the sandbox of
  a connection already saved in FlexigoTech Cloud no longer change (create a new connection instead).
- The unused report client (it sent `as_of_date`, which QuickBooks Online reports do not take) and the query helper
  that never ended are gone.
- Load of the master data from the staging into Odoo (Load on a migration run), in the company of the project and
  archived when inactive in QuickBooks; loading again updates the same records and never creates again what you
  deleted. Accounts are mapped to your chart (an Account Type value of the project's mapping set, else the account
  number) and only created when the mapping gives the code of a new one; customers and vendors become partners,
  inventory, non-inventory and service items products (with their income and expense accounts once those are
  loaded), terms with due days payment terms (with their early payment discount), and classes and departments
  analytic accounts in the plan of the dimension mapping. A tax code is never created as a tax; date-driven terms
  and unmapped accounts, classes and departments are listed in the run instead of being guessed.
- The Trial Balance becomes ONE posted opening entry at its end date, in the first general journal of the company
  (the one Odoo's own opening entry takes): one line per account with its balance (accounts grouped in sections of
  the report included; section totals are not counted), and the receivable and payable
  accounts one line per open invoice or bill (customer or vendor, its number as label, due date and open balance, in
  its own currency when it has another), so you match them with payments in Odoo; no invoice or bill is created, so
  no tax or income is counted twice. A Trial Balance that does not balance creates nothing, and loading again
  duplicates nothing. Then Odoo's balance at that date is compared with QuickBooks', account by account, and each
  receivable and payable account with its open items, in the reconciliation of the project: a difference of more
  than 0.01 blocks Reconciled.
- Unapplied credit memos and vendor credits are open items of the opening entry too, with the opposite sign (a credit
  on the receivables, a debit on the payables), with their customer or vendor and in their own currency at their
  exchange rate, so the receivables and payables tie-outs match QuickBooks. Every credit memo and vendor credit is
  extracted (QuickBooks cannot be asked for only the open ones); the fully applied ones give no line.
- The stock at the cut-off: QuickBooks' Inventory Valuation Summary at the date of the Trial Balance (extracted with
  it) gives the quantity and the asset value of each inventory item, and they go into the main warehouse of the
  company of the project, on the products the items became, valued at QuickBooks' value (its cost is the value over
  the quantity). They come from a location of their own, "QuickBooks opening stock", with no valuation account, so no
  stock valuation entry is made, in perpetual or periodic valuation: the inventory account keeps the balance of the
  opening entry, and Odoo's stock closing finds nothing to post. An item that is not loaded as a storable product, or
  below zero, is listed and no stock is loaded; loading again moves nothing twice and does not put back the purchase
  cost of the items. Odoo's value against QuickBooks', item by item, goes to the reconciliation (Inventory): a
  difference of more than 0.01 blocks Reconciled. The names of the report's columns (Qty, Asset Value) are not yet
  checked against a QuickBooks sandbox.

## 19.0.1.0.7 (2026-11-07)
- App Store listing: the manifest no longer links to an external website (its description gave a web address for service enquiries; now the e-mail) and the page links only to the support e-mail and to FlexigoTech apps on Odoo Apps (Odoo Apps vendor guidelines). No functional change.

## 19.0.1.0.6 (2026-11-07)
- Only a Migration Manager creates, edits and deletes the QuickBooks object catalogue, staging rows, value
  mappings and Class/Location maps; every other internal user reads them. Before, any internal user could
  change or delete them, while the connection was already for Migration Managers only.

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

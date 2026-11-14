====================================
QuickBooks Migration to Odoo 19
====================================

**Module:** ``flexigo_quickbooks_to_odoo_19_accounting_migration_wiza``
**License:** OPL-1
**Author:** FlexigoTech

What it does
============

It moves your QuickBooks books into Odoo 19 once, at a cut-off date. It is a migration, not a synchronization: it
does not keep QuickBooks and Odoo in step after the move, and it does not bring changes made afterwards.

From **QuickBooks Online** (Intuit's Accounting API) it brings:

- accounts, payment terms, classes, departments, customers, vendors and items (tax codes are read into the staging
  but never created as taxes);
- the Trial Balance at the day of the extraction, which becomes one posted opening entry, with one line per open
  invoice, bill, credit memo and vendor credit on the receivable and payable accounts;
- the stock of the Inventory Valuation Summary at the same date, into the main warehouse;
- a reconciliation of Odoo against QuickBooks, account by account, receivables and payables by open item, and stock
  item by item.

From **QuickBooks Desktop** it reads the IIF export of the lists (in QuickBooks Desktop: File > Utilities > Export >
Lists to IIF Files): accounts, classes, customers and vendors (name, email and phone) and items. The opening entry,
the open items and the stock come only from QuickBooks Online; other lists and transactions in the file are not
read.

Old invoices, bills and payments are not imported: the history stays in QuickBooks.

Requirements
============

- Odoo 19, Community or Enterprise, with the FlexigoTech Migration Base module and FlexigoTech Cloud (installed with
  this one).
- For QuickBooks Online: your own Intuit app (client ID and secret), created in your Intuit Developer account.
- The extraction and connecting a new QuickBooks Online connection need the FlexigoTech Cloud trial or subscription
  (Settings > FlexigoTech Cloud). The load, the opening entry, the stock, the reconciliation and everything already
  extracted or loaded work without it.

Installation and upgrade
========================

Install it from the Odoo App Store. Coming from a version before 1.1.0: put ``flexigotech_cloud`` in the same addons
path, restart and update FlexigoTech Migration Base (Apps > FlexigoTech Migration Base > Upgrade, or
``-u flexigo_migration_base``), which updates this module too; restarting without updating does not load it.

Connections
===========

Migration > QuickBooks Connections. Only members of the Migration Manager group create or change connections.

**QuickBooks Online** (type OAuth): enter the client ID of your Intuit app. An administrator (Settings) ticks the
consent and enters the client secret: both go to FlexigoTech Cloud, which keeps them and the QuickBooks tokens
encrypted; Odoo keeps only the client ID. Add the Redirect URI shown on the connection to your Intuit app, then press
Connect to QuickBooks Online and Test Connection.

Connections made before 1.1.0 keep working as bought, directly from Odoo, with their secret and tokens encrypted in
Odoo (``MIGRATION_ENCRYPTION_KEY`` when it is a valid Fernet key, else a key derived from the database secret). When
QuickBooks answers that their access token expired (HTTP 401), Odoo renews it once with the refresh token at Intuit's
token endpoint, stores the new tokens encrypted and asks again; if Intuit does not renew it, the message says to
connect again with Connect to QuickBooks Online. To extract, create a new connection: it goes through FlexigoTech
Cloud.

**QuickBooks Desktop** (type File Upload): upload the IIF export of the lists and press Test Connection. It says how
many accounts, classes, customers, vendors and items Extract will read, which lists of the file it does not read, or
why the file cannot be read.

Migrating
=========

1. **Project** (Migration > Migration Projects): its company is where everything is loaded.
2. **Extract** (a migration run of type Extract, with the connection): the records go into the staging of the
   project, 1,000 a page, as QuickBooks gives them. Nothing is written in your accounting yet: look at the staging
   (Migration > Staging) and set your mappings before loading. This is the check before
   the load; there is no other dry-run mode.
3. **Mappings** (the mapping set of the project): accounts by an Account Type value (source: the Id, number or name
   of the QuickBooks account; target: an Odoo account, or the code of a new account) or else by account number;
   classes and departments by the dimension mapping (an analytic account, the plan to create it in, or Do Not
   Migrate).
4. **Load** (a migration run of type Load): creates the records in the company of the project, archived when they are
   inactive in QuickBooks. Loading again updates the same records, never creates again what you deleted, and posts
   the opening entry and the stock only once. What cannot be loaded (an unmapped account, class or department, a
   date-driven payment term) is listed in the run instead of being guessed. A tax code is never created as a tax.
5. **Reconcile**: the reconciliation of the project shows QuickBooks against Odoo; a difference of more than 0.01
   blocks the Reconciled status. Members of QuickBooks Migration / Reviewer sign off each line with Sign Off.
6. **Delete the staging**: once the project is loaded, a Migration Manager can press Delete Staging on the project.
   It deletes the records as QuickBooks gave them, with the names, emails and phones of customers and vendors; what
   was loaded into Odoo stays, and loading again still creates nothing twice. Extract fills it again.

Interruptions
=============

- If FlexigoTech Cloud stops answering or the service ends, the run pauses where it was and goes on by itself when
  it can. The staging is never deleted by it.
- If QuickBooks Online answers that too many requests were sent (HTTP 429) or has a server error (5xx), the page is
  asked again up to 3 times, waiting what QuickBooks says in Retry-After (at most 10 seconds) or else 2, 4 and 8
  seconds. If it still fails, the run stops as Failed with the answer of QuickBooks, keeps what it extracted, and
  Extract goes on from that page.
- Any other error of QuickBooks (such as a 401 when its authorization was withdrawn) stops the run as Failed in the
  same way.

Access
======

- Migration Manager: manages the QuickBooks connections and deletes the staging of a loaded project. Creating
  projects, runs and mapping sets follows the rights of the FlexigoTech Migration Base module.
- QuickBooks Migration / Reviewer: signs off the reconciliation.
- Other internal users: read the QuickBooks catalogue, the staging, the mappings and the reconciliation.
- Each connection, its staging and each project stay in their company. A copy of the database (neutralized) forgets
  the QuickBooks tokens, and uninstalling the module deletes the Intuit apps kept in FlexigoTech Cloud.

Known limitations
=================

- It is tested against a simulator that follows Intuit's documentation and SDK, not yet against a real QuickBooks
  Online company.
- The Trial Balance and the Inventory Valuation Summary must be in the currency of the company.
- From QuickBooks Desktop only the lists named above are read.

Support
=======

comercial@flexigobe.com

License
=======

Odoo Proprietary License v1.0 (OPL-1). The changes of each version are in CHANGELOG.md.

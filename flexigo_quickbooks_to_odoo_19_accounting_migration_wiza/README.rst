=====================================
QuickBooks to Odoo 19 Migration Wizard
=====================================

**Module:** ``flexigo_quickbooks_to_odoo_19_accounting_migration_wiza``
**License:** LGPL-3
**Status:** Production Ready
**Author:** FlexigoTech

Overview
========

A wizard-driven, auditable, idempotent toolkit for extracting accounting data from
QuickBooks Online (via Intuit Accounting API) or QuickBooks Desktop (via export upload)
and loading it into Odoo 19 with a repeatable, reconciled cutover process.

This module is **FREE** (LGPL-3 license). The actual migration service is delivered as a paid
implementation engagement by FlexigoTech.

Key Features
============

- **Dual-path connectivity:** QBO OAuth 2.0 API or QBDT export file upload
- **Secure credential storage:** Fernet encryption with environment-based key management
- **Rate-limited extraction:** Configurable concurrency with exponential backoff retry
- **Idempotent loading:** Re-runnable without creating duplicates via source ID tracking
- **Staged validation:** Dry-run mode with detailed error reporting before commit
- **Reconciliation pack:** Trial balance, A/R aging, A/P aging, inventory reports
- **Cutover runbook:** Freeze timestamp, delta sync, go/no-go gate, safe rollback
- **Immutable audit trail:** Append-only run log with per-record lineage
- **PII data governance:** Redactable historical archive for GDPR compliance
- **Multi-company isolation:** Company-scoped access controls

Use Cases
=========

- **QuickBooks Desktop → Odoo 19:** Migrate from legacy on-premise software
- **QuickBooks Online → Odoo 19:** Consolidate cloud-to-cloud cutover
- **Multi-entity migration:** Handle multi-currency and multi-company scenarios
- **Audit-ready data load:** Full traceability and reversibility for financial control

Supported QuickBooks Versions
=============================

- QuickBooks Online 2024 (via Intuit Accounting API)
- QuickBooks Desktop 2020-2024 (via export upload)

Supported Odoo Versions
=======================

- Odoo 19 Community Edition
- Odoo 19 Enterprise Edition

Supported Export Formats
========================

1. **IIF** (Intuit Interchange Format) — Richest fidelity, widest compatibility
2. **qbXML** — Structured format, SDK exports
3. **CSV** — Fallback for manual exports

Installation
============

1. Install the module via the Odoo App Store or direct upload
2. Enable the QuickBooks Migration Wizard from the Apps menu
3. Configure your Intuit OAuth credentials or prepare QBDT export files
4. Create a migration project and follow the wizard

Configuration
==============

**Environment Variables:**

- ``MIGRATION_ENCRYPTION_KEY`` — Fernet encryption key for credential storage (required for production)

**Settings (via Odoo config):**

- ``qbdt_max_upload_size_mb`` — Max QBDT upload size (default: 200 MB)
- ``qb_attachment_max_size_mb`` — Max attachment size (default: 25 MB)
- ``qb_attachment_max_total_mb`` — Max total attachments per run (default: 500 MB)

**OAuth Setup (for QBO users):**

1. Create an Intuit Developer account at https://developer.intuit.com
2. Create an application and obtain Client ID + Client Secret
3. Set up a redirect URI to your Odoo instance
4. In the module, create a connection profile with OAuth credentials
5. Authorize via the "Test Connection" button

**QBDT Setup (for Desktop users):**

1. Export your QuickBooks Desktop data (File → Utilities → Export)
2. Choose the format (IIF, CSV, or qbXML)
3. Upload the export file via the module's upload interface
4. Validate the upload structure via "Test Connection"

Usage
=====

**Basic Workflow:**

1. **Create Project:** Name, source type, target Odoo company
2. **Connect:** Configure OAuth (QBO) or upload (QBDT)
3. **Extract:** Retrieve data from QuickBooks (with resumable checkpoints)
4. **Map:** Configure field and value transformations
5. **Validate:** Dry-run to detect errors before loading
6. **Load:** Execute idempotent batch load with lineage tracking
7. **Reconcile:** Compare balances and aging reports side-by-side
8. **Cutover:** Freeze QuickBooks, perform delta sync, go-live
9. **Archive:** Immutable historical record for audit/GDPR compliance

**Resumability:**

All operations support interruption and resumption. Extraction cursors and load batch
positions are persisted, allowing safe stop/restart without data loss or duplication.

**Dry-Run Mode:**

Validate your mapping and loading strategy without modifying the Odoo database. Useful
for testing before the production cutover.

**Delta Sync:**

After initial load, extract only changed records since the freeze timestamp. Sync
deletions detected via QuickBooks CDC (Change Data Capture) endpoint.

Security
========

**Credential Protection:**

- OAuth tokens, refresh tokens, and API secrets are encrypted via Fernet (AES-128)
- Encryption key is read from environment variable (not stored in database)
- Credentials are never logged, tracked in chatter, or exposed in UI

**Access Control:**

- Migration Manager group: Full CRUD on all migration resources
- Migration Reviewer group: Read on projects/results, sign-off on reconciliation
- Record rules enforce company isolation

**Audit & Compliance:**

- Immutable run log captures every extraction, transformation, and load operation
- Per-record lineage ties each Odoo record to its source QuickBooks ID
- Historical archive supports GDPR data subject access requests (DSAR)
- PII redaction workflow preserves financial data while removing sensitive personal fields

Data Flow
=========

```
QuickBooks ──→ Staging Layer ──→ Validation ──→ Mapping ──→ Loader ──→ Odoo Target
    │                 │              │             │           │
    │                 │              │             │           └─ Upserts on QB ID
    │                 │              │             └─ Value mappings
    │                 │              └─ Dry-run (no commit)
    │                 └─ Staged per source ID + raw payload
    └─ QBO API or QBDT export upload
```

Reconciliation
==============

Post-load reconciliation pack includes:

- **Trial Balance:** QuickBooks TB vs Odoo GL, with materiality tolerance
- **A/R Aging:** Aged receivables detail, tieout by due date bucket
- **A/P Aging:** Aged payables detail, tieout by due date bucket
- **Inventory Valuation:** Summary comparison (caveat: QB costing ≠ Odoo costing without re-valuation)
- **Multi-currency:** Dual-currency reporting for FX-revalued items
- **Sign-off workflow:** Approval gate before cutover to production

Known Limitations
=================

1. **Inventory Costing:** QB FIFO/Weighted Average costs may differ from Odoo after load. Recommendation: perform post-migration revaluation via Odoo stock moves.

2. **Unmatched Dimensions:** QuickBooks Classes or Locations without Odoo analytic accounts will be flagged and optionally skipped.

3. **Custom Fields:** QB custom fields are migrated as-is; Odoo custom field schema must be pre-configured.

4. **Attachments:** Large attachments (>25 MB) are streamed in chunks; network interruption may require re-upload.

5. **RTL Languages:** Reconciliation pack templates do not support RTL/BIDI rendering. Flagged for future expansion to Arabic/Hebrew markets.

Troubleshooting
===============

**OAuth Token Expired:**

Use the "Refresh Token" action on the connection profile to obtain a new access token.

**Extraction Stalled:**

Check the run log for HTTP 429 (rate limit) errors. Reduce concurrency cap and retry.

**Validation Errors:**

Review the validation CSV export for field-by-field error details. Common issues:
- Unmapped dimensions (Classes/Locations)
- Duplicate account numbers
- Missing required fields (e.g., account type for GL accounts)

**Rollback:**

Use the "Rollback to Checkpoint" action to revert the load to the pre-migration state.
Rollback is safe only if no subsequent transactions were posted in Odoo.

Support & Services
==================

For migration planning, configuration, and implementation support:

👉 `FlexigoTech Services <https://flexigotech.com/services>`__

Free module documentation: `FlexigoTech Docs <https://docs.flexigotech.com>`__

License
=======

This module is licensed under the **LGPL-3** license. See LICENSE file for details.

Changelog
=========

**1.0.0 (2026-06-14)**

- Initial release
- Support for QBO Online API and Desktop export
- Complete cutover workflow with reconciliation and rollback
- Encrypted credential storage and immutable audit trail

# SpendRecon

SpendRecon is a backend extraction engine for advertising spend reconciliation. It ingests raw invoice, credit-note, bank-statement, and generic CSV/Excel/PDF files and normalizes them into structured financial records that downstream systems can reconcile.

## Archetype

**Document Reconciler / Backend Processing Service.**

- Deterministic, stateless extraction: raw bytes in, normalized records out.
- No database dependency at the processing layer; the poller owns persistence.
- Fail-soft: malformed input returns an empty list rather than raising.

## Repository Layout

| File | Purpose |
|------|---------|
| `processor.py` | Core extraction engine. Reads bytes, tries PDF via `pdfplumber`, then Excel via `openpyxl`, then UTF-8/CSV fallback. Parses ad-platform invoices/credit notes, bank statements, and generic CSVs. Returns `list[dict]` with top-level `title`, `status`, `details`, `due_date`. |
| `run_demo.py` | Zero-argument demo/smoke test using hardcoded CSV bytes. Exits 0 in under 10 seconds. |
| `run_tests.py` | Minimal test suite for CSV and bank-statement parsing. |
| `requirements.txt` | Python dependencies (`pdfplumber`, `openpyxl`). |

## Record Contract

Every record returned by `process_file(data, source_name=None)` is a dict with four top-level keys:

- `title` - human-readable label, e.g. `Invoice INV-2024-001` or the bank description.
- `status` - normalized lifecycle state: `unpaid`, `credited`, `reconciled`, `paid`, or `pending`.
- `details` - canonicalized field map. Ad invoices use keys such as `source_platform`, `document_type`, `invoice_number`, `invoice_date`, `due_date`, `advertiser_or_client_name`, `currency`, `subtotal`, `tax_amount`, `total_amount`, `payment_method`, `payment_reference`, `invoice_status`. Bank statements use keys such as `bank_account_name`, `bank_account_number`, `transaction_id`, `transaction_date`, `bank_description`, `payee`, `amount`, `currency`, `running_balance`, `statement_balance`.
- `due_date` - top-level ISO-ish date string promoted from details, or `None`.

## Detection Order

1. **PDF** - files beginning with the `%PDF-` magic bytes are parsed with `pdfplumber` and matched against regex field patterns.
2. **Excel** - ZIP-magic files (`PK\x03\x04` / `PK\x05\x06`) are parsed with `openpyxl` in read-only mode across all worksheets.
3. **CSV / UTF-8** - everything else is decoded as `utf-8-sig`, `utf-8`, then `latin-1`, sniffed for a delimiter (`,`, `;`, tab, `|`), and mapped through alias tables.

Document type is inferred from normalized header names: ad-invoice aliases take priority, then bank-statement aliases, otherwise generic passthrough.

## What the Poller Expects as Input

The Railway poller is responsible for fetching source files and handing raw bytes to this engine.

**Poller -> `process_file`:**

- `data` (required): `bytes` (or `bytearray`). A `str` is accepted and UTF-8 encoded. `None` or unsupported types return `[]`.
- `source_name` (optional): a label used only for `title` fallback, typically the original filename or object key.

**`process_file` -> Poller:**

- `list[dict]` where each dict has `title`, `status`, `details`, `due_date`.
- Never raises on malformed input; an unreadable file yields `[]`.

Supported input formats: `.pdf`, `.xlsx`/`.xlsm`, `.csv`, and delimited text. Supported source content: ad-platform invoices and credit notes, bank statements, and generic tabular exports.

## Running

```
pip install -r requirements.txt
python3 run_demo.py
python3 run_tests.py
```

Both scripts are zero-argument and exit nonzero on failure.

Dashboard: https://spendrecon.vokrix.co
Vercel: spendrecon
Railway: spendrecon
Cloudflare: spendrecon.vokrix.co

Billing: price_1UHYbD2c9uGCcgMS2YKYIiBK
Landing: https://vokrix.co/spendrecon

Outreach: active

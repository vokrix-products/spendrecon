"""SpendRecon core extraction engine.

Reads raw invoice/statement/CSV bytes and normalizes them into a list of
dicts. Each record has top-level keys: title, status, details, due_date.

Extraction order: PDF (pdfplumber) -> Excel (openpyxl) -> CSV/UTF-8 fallback.
"""

import io
import re
import csv
from datetime import datetime, date

import pdfplumber
import openpyxl


_BANK_MAP = {
    "bank_account_name": "bank_account_name",
    "bank_account_number": "bank_account_number",
    "account_number": "bank_account_number",
    "transaction_id": "transaction_id",
    "transaction_date": "transaction_date",
    "date": "transaction_date",
    "bank_description": "bank_description",
    "description": "bank_description",
    "bank_desc": "bank_description",
    "payee": "payee",
    "amount": "amount",
    "currency": "currency",
    "running_balance": "running_balance",
    "balance": "running_balance",
    "statement_balance": "statement_balance",
    "upload_id": "upload_id",
}

_AD_MAP = {
    "source_platform": "source_platform",
    "platform": "source_platform",
    "document_type": "document_type",
    "type": "document_type",
    "invoice_number": "invoice_number",
    "invoice_no": "invoice_number",
    "document_number": "document_number",
    "document_no": "document_number",
    "invoice_date": "invoice_date",
    "date": "invoice_date",
    "due_date": "due_date",
    "billing_period_start": "billing_period_start",
    "period_start": "billing_period_start",
    "billing_period_end": "billing_period_end",
    "period_end": "billing_period_end",
    "advertiser_or_client_name": "advertiser_or_client_name",
    "advertiser": "advertiser_or_client_name",
    "advertiser_name": "advertiser_or_client_name",
    "client": "advertiser_or_client_name",
    "client_name": "advertiser_or_client_name",
    "advertiser_id": "advertiser_id",
    "ad_account_id": "ad_account_id",
    "account_id": "ad_account_id",
    "ad_account_name": "ad_account_name",
    "account_name": "ad_account_name",
    "currency": "currency",
    "subtotal": "subtotal",
    "sub_total": "subtotal",
    "tax_amount": "tax_amount",
    "tax": "tax_amount",
    "total_amount": "total_amount",
    "total": "total_amount",
    "amount_due": "total_amount",
    "adjustments": "adjustments",
    "refunds": "refunds",
    "refund": "refunds",
    "payment_method": "payment_method",
    "payment_reference": "payment_reference",
    "payment_ref": "payment_reference",
    "invoice_status": "invoice_status",
    "upload_id": "upload_id",
}

_AD_SPECIFIC_ALIASES = {
    "source_platform", "platform", "document_type", "type",
    "invoice_number", "invoice_no", "document_number", "document_no",
    "invoice_date", "due_date", "billing_period_start", "period_start",
    "billing_period_end", "period_end", "advertiser_or_client_name",
    "advertiser", "advertiser_name", "client", "client_name",
    "advertiser_id", "ad_account_id", "account_id", "ad_account_name",
    "account_name", "subtotal", "sub_total", "tax_amount", "tax",
    "total_amount", "total", "amount_due", "adjustments", "refunds",
    "refund", "payment_method", "payment_reference", "payment_ref",
    "invoice_status",
}

_BANK_SPECIFIC_ALIASES = {
    "bank_account_name", "bank_account_number", "account_number",
    "transaction_id", "transaction_date", "bank_description",
    "description", "bank_desc", "payee", "amount", "running_balance",
    "balance", "statement_balance",
}


# Contract statuses. Anything outside this set violates the record contract.
ALLOWED_STATUSES = ("unpaid", "credited", "reconciled", "paid", "pending")

_STATUS_ALIASES = {
    "open": "unpaid",
    "outstanding": "unpaid",
    "due": "unpaid",
    "not_paid": "unpaid",
    "credit": "credited",
    "credit_note": "credited",
    "credit_note_issued": "credited",
    "matched": "reconciled",
    "settled": "reconciled",
    "reconciliation": "reconciled",
    "closed": "paid",
    "complete": "paid",
    "completed": "paid",
    "extracted": "pending",
    "processing": "pending",
    "draft": "pending",
    "unknown": "pending",
    "n_a": "pending",
}


# Fields a generic (header-agnostic) record can use to build a real title.
_GENERIC_TITLE_FIELDS = (
    "invoice_number", "document_number", "transaction_id", "reference",
    "reference_number", "reference_no", "external_id", "record_id",
    "payee", "bank_description", "description",
    "advertiser_or_client_name", "client_name", "customer_name", "name",
    "label", "title",
)


def _normalize_key(value):
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def _detect_doc_type_from_headers(raw_headers):
    norm = [_normalize_key(h) for h in raw_headers if h]
    if any(h in _AD_SPECIFIC_ALIASES for h in norm):
        return "ad_invoice"
    if any(h in _BANK_SPECIFIC_ALIASES for h in norm):
        return "bank_statement"
    return "generic"


def _detect_doc_type_from_details(details):
    keys = set(details.keys())
    if keys & {"invoice_number", "source_platform", "total_amount",
               "advertiser_or_client_name", "document_type"}:
        return "ad_invoice"
    if keys & {"transaction_id", "running_balance", "bank_description",
               "bank_account_number", "transaction_date"}:
        return "bank_statement"
    return "generic"


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s if s != "" else None
    return value


def _parse_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y",
        "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S",
        "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
    ):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def _parse_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    s = str(value).strip()
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = re.sub(r"[^0-9.\-]", "", s)
    if s in ("", "-", ".", "-."):
        return None
    try:
        num = float(s)
    except ValueError:
        return None
    if neg:
        num = -num
    return num


def _to_canonical(raw_row, mapping):
    out = {}
    for raw_key, raw_val in raw_row.items():
        if raw_key is None:
            continue
        nk = _normalize_key(raw_key)
        target = mapping.get(nk)
        if target is None:
            continue
        if target in out and out[target] is not None:
            continue
        out[target] = _clean(raw_val)
    return out


_DATE_FIELDS = {
    "transaction_date", "invoice_date", "due_date",
    "billing_period_start", "billing_period_end",
}

_AMOUNT_FIELDS = {
    "amount", "running_balance", "statement_balance", "subtotal",
    "tax_amount", "total_amount", "adjustments", "refunds",
}


def _post_process_details(details):
    for f in _DATE_FIELDS:
        if f in details:
            details[f] = _parse_date(details[f])
    for f in _AMOUNT_FIELDS:
        if f in details:
            details[f] = _parse_number(details[f])
    if "currency" in details and details["currency"]:
        details["currency"] = str(details["currency"]).strip().upper()
    return details


def _normalize_status(value, default="pending"):
    """Clamp a raw status onto the record contract's allowed set."""
    if value is None:
        return default
    key = _normalize_key(value)
    if not key:
        return default
    if key in ALLOWED_STATUSES:
        return key
    return _STATUS_ALIASES.get(key, default)


def _source_label(source_name):
    if not source_name:
        return None
    base = str(source_name).strip().rstrip("/")
    if base.startswith("uploads/"):
        base = base[len("uploads/"):]
    base = base.split("/")[-1]
    return base or None


def _generic_title(details, source_name=None):
    """Build a real title for header-agnostic records.

    Falls back in order: identifying field -> any non-numeric field ->
    source file name -> last resort.
    """
    for field in _GENERIC_TITLE_FIELDS:
        val = details.get(field)
        if val is None or str(val).strip() == "":
            continue
        val = str(val).strip()
        if field == "title":
            return val
        label = " ".join(p.capitalize() for p in field.split("_"))
        return "{} {}".format(label, val)
    for key in sorted(details):
        if key in _DATE_FIELDS or key in _AMOUNT_FIELDS:
            continue
        if key in ("currency", "status", "invoice_status"):
            continue
        val = details.get(key)
        if val is None or str(val).strip() == "":
            continue
        return str(val).strip()
    label = _source_label(source_name)
    if label:
        return label
    return "Unlabeled Record"


def _build_record(details, source_name=None):
    details = {k: v for k, v in details.items()
               if v is not None and v != ""}
    details = _post_process_details(details)
    dtype = _detect_doc_type_from_details(details)
    if dtype == "bank_statement":
        title = details.get("bank_description") or _generic_title(
            details, source_name)
        status = _normalize_status(
            details.get("status") or details.get("invoice_status"),
            "reconciled")
        due_date = details.get("transaction_date")
    elif dtype == "ad_invoice":
        inv = details.get("invoice_number") or details.get("document_number")
        title = "Invoice {}".format(inv) if inv else _generic_title(
            details, source_name)
        status = _normalize_status(
            details.get("invoice_status") or details.get("status"),
            "unpaid")
        due_date = details.get("due_date")
    else:
        title = _generic_title(details, source_name)
        status = _normalize_status(
            details.get("status") or details.get("invoice_status"),
            "pending")
        due_date = details.get("due_date")
    details["status"] = status
    return {
        "title": title,
        "status": status,
        "details": details,
        "due_date": due_date,
    }


def _rows_to_records(rows, source_name=None):
    rows = [r for r in rows if r]
    if not rows:
        return []
    header = rows[0]
    norm_headers = [_normalize_key(h) for h in header]
    doc_type = _detect_doc_type_from_headers(header)
    mapping = _AD_MAP if doc_type == "ad_invoice" else (
        _BANK_MAP if doc_type == "bank_statement" else {})
    records = []
    if doc_type in ("ad_invoice", "bank_statement"):
        seen_ok = False
        for row in rows[1:]:
            if not any(_clean(c) is not None for c in row):
                continue
            raw = {}
            for idx, cell in enumerate(row):
                if idx < len(header):
                    raw[header[idx]] = cell
            details = _to_canonical(raw, mapping)
            if not details:
                continue
            seen_ok = True
            records.append(_build_record(details, source_name))
        if seen_ok:
            return records
    header_map = []
    for h in header:
        header_map.append(_normalize_key(h))
    for row in rows[1:]:
        if not any(_clean(c) is not None for c in row):
            continue
        details = {}
        for idx, cell in enumerate(row):
            if idx < len(header):
                key = header_map[idx] or "column_{}".format(idx)
                val = _clean(cell)
                if val is not None:
                    details[key] = val
        if not details:
            continue
        records.append(_build_record(details, source_name))
    return records


def _extract_text_from_pdf(data):
    text_parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            text_parts.append(t)
    return "\n".join(text_parts)


def _parse_pdf_metadata(data, source_name=None):
    records = []
    try:
        text = _extract_text_from_pdf(data)
    except Exception:
        return records
    if not text.strip():
        return records
    fields = {}
    patterns = {
        "invoice_number": r"(?:invoice|document)\s*(?:number|no|#)\s*[:\-]?\s*([A-Za-z0-9\-_/]+)",
        "due_date": r"due\s*date\s*[:\-]?\s*([0-9A-Za-z,./\- ]+)",
        "invoice_date": r"invoice\s*date\s*[:\-]?\s*([0-9A-Za-z,./\- ]+)",
        "total_amount": r"(?:total|amount\s*due|balance\s*due)\s*[:\-]?\s*\$?\s*([0-9,]+\.?[0-9]*)",
        "advertiser_or_client_name": r"(?:bill\s*to|advertiser|client)\s*[:\-]?\s*([A-Za-z0-9 ,.&'\-]+)",
    }
    for field, pat in patterns.items():
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            fields[field] = m.group(1).strip()
    if fields:
        records.append(_build_record(fields, source_name))
    return records


def _extract_from_excel(data, source_name=None):
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True,
                                data_only=True)
    all_records = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append(list(row))
        all_records.extend(_rows_to_records(rows, source_name))
    wb.close()
    return all_records


def _extract_from_csv(data, source_name=None):
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except (UnicodeDecodeError, AttributeError):
            continue
    if text is None:
        return []
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = []
    for row in reader:
        if any(_clean(c) is not None for c in row):
            rows.append(row)
    return _rows_to_records(rows, source_name)


def process_file(data, source_name=None):
    """Extract structured records from raw file bytes.

    Returns a list[dict], each with top-level keys:
        title, status, details, due_date
    """
    if data is None:
        return []
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, (bytes, bytearray)):
        return []

    # 1. PDF
    if data[:5] == b"%PDF-":
        records = _parse_pdf_metadata(data, source_name)
        if records:
            return records

    # 2. Excel (xlsx / xlsm / xltx)
    if data[:2] == b"PK" and data[2:4] in (b"\x03\x04", b"\x05\x06"):
        try:
            records = _extract_from_excel(data, source_name)
            if records:
                return records
        except Exception:
            pass

    # 3. CSV / UTF-8 fallback
    records = _extract_from_csv(data, source_name)
    return records


def main():
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], "rb") as fh:
            data = fh.read()
        src = sys.argv[2] if len(sys.argv) > 2 else sys.argv[1]
    else:
        data = b"invoice_number,total_amount,due_date\nINV-1,100.00,2024-01-31\n"
        src = "stdin"
    for rec in process_file(data, src):
        print(rec)


if __name__ == "__main__":
    main()

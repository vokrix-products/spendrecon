"""Minimal test suite for the SpendRecon processor.

Run with:  python3 run_tests.py

Covers CSV invoice parsing, bank-statement parsing, and the generic
header-agnostic path that must still honour the record contract.
"""
import sys

from processor import ALLOWED_STATUSES, process_file

AD_CSV = (
    "invoice_number,invoice_date,due_date,advertiser_or_client_name,"
    "currency,total_amount,invoice_status\n"
    "INV-100,2024-03-01,2024-03-31,Globex Inc,USD,2500.50,unpaid\n"
)

BANK_CSV = (
    "bank_account_name,transaction_date,description,amount,currency,"
    "running_balance\n"
    "Globex Operating,2024-03-15,Card Payment,-2500.50,USD,10000.00\n"
)

# Unrecognized headers -> generic path (this is what test_1/2/3.csv hit).
GENERIC_CSV = (
    "currency,invoice date,due date\n"
    "USD,2024-03-01,2024-03-31\n"
)

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print("PASS: {}".format(label))
    else:
        _failed += 1
        print("FAIL: {}".format(label))


def test_returns_list():
    recs = process_file(AD_CSV.encode("utf-8"), "ad.csv")
    check("process_file returns a list", isinstance(recs, list))


def test_ad_invoice_extraction():
    recs = process_file(AD_CSV.encode("utf-8"), "ad.csv")
    check("one ad invoice record", len(recs) == 1)
    if recs:
        rec = recs[0]
        d = rec["details"]
        check("ad title contains invoice number",
              "INV-100" in rec["title"])
        check("ad status parsed", rec["status"] == "unpaid")
        check("ad due_date top-level", rec["due_date"] == "2024-03-31")
        check("ad total_amount canonical",
              d.get("total_amount") == 2500.50)
        check("ad currency canonical", d.get("currency") == "USD")
        check("ad advertiser canonical",
              d.get("advertiser_or_client_name") == "Globex Inc")
        check("ad invoice_date parsed",
              d.get("invoice_date") == "2024-03-01")


def test_bank_statement_extraction():
    recs = process_file(BANK_CSV.encode("utf-8"), "bank.csv")
    check("one bank record", len(recs) == 1)
    if recs:
        rec = recs[0]
        d = rec["details"]
        check("bank status reconciled", rec["status"] == "reconciled")
        check("bank description canonical",
              d.get("bank_description") == "Card Payment")
        check("bank amount parsed", d.get("amount") == -2500.50)
        check("bank running_balance parsed",
              d.get("running_balance") == 10000.00)
        check("bank transaction_date parsed",
              d.get("transaction_date") == "2024-03-15")


def test_generic_records_contract():
    recs = process_file(GENERIC_CSV.encode("utf-8"), "test_1.csv")
    check("generic csv returns one record", len(recs) == 1)
    if recs:
        rec = recs[0]
        check("generic status is in allowed set",
              rec["status"] in ALLOWED_STATUSES)
        check("generic status is pending", rec["status"] == "pending")
        check("generic title is not the placeholder",
              rec["title"] != "Extracted Record")
        check("generic title falls back to source name",
              rec["title"] == "test_1.csv")
        check("generic status mirrored into details",
              rec["details"].get("status") == "pending")


def test_generic_title_from_own_fields():
    # "amount" is a bank alias, so header detection classifies this as a
    # bank statement even though the identifying column is "reference".
    # The alias table must not swallow that column.
    recs = process_file(
        b"reference,currency,amount\nACME-77,USD,120.00\n", "ref.csv")
    check("generic title built from own field",
          bool(recs) and "ACME-77" in recs[0]["title"])
    check("unmapped column preserved in details",
          bool(recs) and recs[0]["details"].get("reference") == "ACME-77")
    check("mapped columns still canonical on that path",
          bool(recs) and recs[0]["details"].get("amount") == 120.00)


def test_alias_path_keeps_extra_columns():
    # Fully-mapped ad invoice plus an unknown column: the canonical
    # fields must survive and the extra column must not be lost.
    recs = process_file(
        b"invoice_number,currency,total_amount,po_reference\n"
        b"INV-200,USD,99.00,PO-9001\n", "ad-extra.csv")
    check("extra column does not break ad detection",
          bool(recs) and recs[0]["title"] == "Invoice INV-200")
    check("extra column retained",
          bool(recs) and recs[0]["details"].get("po_reference") == "PO-9001")


def test_status_normalization():
    recs = process_file(
        b"status,title\nweird_state,Widget Co\n", "statuses.csv")
    check("unknown status clamped to pending",
          bool(recs) and recs[0]["status"] == "pending")
    check("details status matches top-level",
          bool(recs) and recs[0]["details"].get("status")
          == recs[0]["status"])
    recs = process_file(
        b"status,title\nextracted,Widget Co\n", "statuses.csv")
    check("legacy 'extracted' status mapped to pending",
          bool(recs) and recs[0]["status"] == "pending")


def test_invoice_id_used_for_title():
    # Ad invoices that label the document column "invoice_id" rather
    # than "invoice_number" must still get a real invoice title.
    recs = process_file(
        b"invoice_id,currency,total_amount,due_date\n"
        b"INV-500,USD,10.00,2024-05-01\n", "inv-id.csv")
    check("title built from invoice_id",
          bool(recs) and recs[0]["title"] == "Invoice INV-500")
    check("invoice_id preserved in details",
          bool(recs) and recs[0]["details"].get("invoice_id") == "INV-500")


def test_invoice_id_preferred_over_transaction_id():
    # The real test_1.csv shape: the identifying column is invoice_id and
    # a transaction_id sits beside it. The title must not be the
    # transaction id.
    recs = process_file(
        b"invoice_id,transaction_id,amount,currency\n"
        b"INV-2024-0871,TXN-884201934,14825.50,EUR\n", "test_1.csv")
    check("invoice_id preferred over transaction_id",
          bool(recs) and "INV-2024-0871" in recs[0]["title"])
    check("title is not the transaction id",
          bool(recs) and "TXN-884201934" not in recs[0]["title"])


def test_no_status_outside_contract():
    for src in (AD_CSV, BANK_CSV, GENERIC_CSV):
        for rec in process_file(src.encode("utf-8"), "mix.csv"):
            check("status within contract for {}".format(
                src.split("\n")[0][:24]),
                rec["status"] in ALLOWED_STATUSES)
            check("title is non-empty string",
                  isinstance(rec["title"], str) and rec["title"].strip())


def test_empty_input():
    check("empty bytes returns empty list",
          process_file(b"", "empty.csv") == [])
    check("None returns empty list", process_file(None, "none.csv") == [])


def main():
    test_returns_list()
    test_ad_invoice_extraction()
    test_bank_statement_extraction()
    test_generic_records_contract()
    test_generic_title_from_own_fields()
    test_alias_path_keeps_extra_columns()
    test_status_normalization()
    test_invoice_id_used_for_title()
    test_invoice_id_preferred_over_transaction_id()
    test_no_status_outside_contract()
    test_empty_input()
    print("\n{} passed, {} failed".format(_passed, _failed))
    if _failed:
        return 1
    print("All tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

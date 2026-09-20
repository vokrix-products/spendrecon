"""Minimal test suite for the SpendRecon processor.

Run with:  python3 run_tests.py

Covers CSV invoice parsing and bank-statement parsing.
"""
import sys

from processor import process_file

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


def test_empty_input():
    check("empty bytes returns empty list",
          process_file(b"", "empty.csv") == [])
    check("None returns empty list", process_file(None, "none.csv") == [])


def main():
    test_returns_list()
    test_ad_invoice_extraction()
    test_bank_statement_extraction()
    test_empty_input()
    print("\n{} passed, {} failed".format(_passed, _failed))
    if _failed:
        return 1
    print("All tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

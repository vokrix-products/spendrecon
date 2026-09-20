"""Zero-argument demo / smoke test for the SpendRecon processor.

Run with:  python3 run_demo.py

Uses hardcoded CSV bytes, runs process_file, asserts a list is returned,
and exits 0 in under 10 seconds.
"""
import sys
import time

from processor import process_file

DEMO_CSV = (
    "source_platform,document_type,invoice_number,invoice_date,"
    "due_date,advertiser_or_client_name,currency,subtotal,tax_amount,"
    "total_amount,invoice_status\n"
    "google_ads,invoice,INV-2024-001,2024-01-01,2024-01-31,"
    "Acme Corp,USD,1000.00,100.00,1100.00,unpaid\n"
    "meta_ads,credit_note,CN-2024-014,2024-01-05,2024-02-04,"
    "Acme Corp,USD,-200.00,-20.00,-220.00,credited\n"
)

DEMO_BANK_CSV = (
    "bank_account_name,bank_account_number,transaction_id,"
    "transaction_date,bank_description,payee,amount,currency,"
    "running_balance\n"
    "Acme Operating,1234567890,TXN-9001,2024-01-15,"
    "ACH Payment,Google Ads,-1100.00,USD,5400.00\n"
)


def main():
    start = time.time()

    ad_records = process_file(DEMO_CSV.encode("utf-8"), "demo_invoices.csv")
    bank_records = process_file(
        DEMO_BANK_CSV.encode("utf-8"), "demo_bank.csv"
    )

    assert isinstance(ad_records, list), "process_file must return a list"
    assert isinstance(bank_records, list), "process_file must return a list"
    assert ad_records, "expected at least one ad invoice record"
    assert bank_records, "expected at least one bank record"

    for rec in ad_records + bank_records:
        assert isinstance(rec, dict), "each record must be a dict"
        for key in ("title", "status", "details", "due_date"):
            assert key in rec, "record missing top-level key: " + key
        assert isinstance(rec["details"], dict), "details must be a dict"

    print("Ad invoice records: {}".format(len(ad_records)))
    for rec in ad_records:
        print("  - {} [{}] due {}".format(
            rec["title"], rec["status"], rec["due_date"]))
    print("Bank records: {}".format(len(bank_records)))
    for rec in bank_records:
        print("  - {} [{}] due {}".format(
            rec["title"], rec["status"], rec["due_date"]))

    elapsed = time.time() - start
    assert elapsed < 10, "demo must finish in under 10 seconds"
    print("Demo OK in {:.3f}s".format(elapsed))
    return 0


if __name__ == "__main__":
    sys.exit(main())

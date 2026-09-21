import os
import time
import json
import datetime
import traceback
import requests

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_SERVICE_KEY = os.environ['SUPABASE_SERVICE_KEY']
PRODUCT_ID = os.environ['PRODUCT_ID']
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

REST_URL = f"{SUPABASE_URL}/rest/v1"
SB_HEADERS = {
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "apikey": SUPABASE_SERVICE_KEY,
}

UPLOADS_BUCKET = "uploads"
RESULTS_BUCKET = "results"


def download_file(bucket, file_path):
    if file_path.startswith(bucket + "/"):
        file_path = file_path[len(bucket) + 1:]
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{file_path}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "apikey": SUPABASE_SERVICE_KEY})
    resp.raise_for_status()
    return resp.content


def upload_file(bucket, file_path, data):
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{file_path}"
    resp = requests.post(url, headers={**SB_HEADERS, "Content-Type": "application/octet-stream"}, data=data)
    resp.raise_for_status()
    return f"{bucket}/{file_path}"


def get_pending_jobs():
    url = f"{REST_URL}/jobs"
    params = {
        "status": "eq.pending",
        "job_type": "eq.process_upload",
        "product_id": f"eq.{PRODUCT_ID}",
        "select": "*",
        "order": "created_at.asc",
        "limit": "10",
    }
    resp = requests.get(url, headers=SB_HEADERS, params=params)
    resp.raise_for_status()
    return resp.json()


def delete_records_for_source(source_file_path):
    """Drop this product's previous records for one source file.

    Makes reprocessing a file idempotent instead of appending a second
    set of records. Scoped to PRODUCT_ID because the records table is
    shared across products.
    """
    url = f"{REST_URL}/records"
    headers = {**SB_HEADERS, "Prefer": "return=minimal"}
    params = {
        "product_id": f"eq.{PRODUCT_ID}",
        "source_file_path": f"eq.{source_file_path}",
    }
    resp = requests.delete(url, headers=headers, params=params)
    resp.raise_for_status()


def insert_record(record):
    url = f"{REST_URL}/records"
    headers = {**SB_HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}
    resp = requests.post(url, headers=headers, json=record)
    resp.raise_for_status()


def update_job(job_id, status, output_file_path=None, result_summary=None):
    url = f"{REST_URL}/jobs?id=eq.{job_id}"
    headers = {**SB_HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}
    payload = {
        "status": status,
        "completed_at": datetime.datetime.utcnow().isoformat(),
    }
    if output_file_path is not None:
        payload["output_file_path"] = output_file_path
    if result_summary is not None:
        payload["result_summary"] = result_summary
    resp = requests.patch(url, headers=headers, json=payload)
    resp.raise_for_status()


def insert_notification(job, success: bool):
    title = "Processing complete" if success else "Processing failed"
    body = "Your upload has been processed successfully." if success else "There was an error processing your upload."
    notification_type = "success" if success else "error"
    notification = {
        "product_id": PRODUCT_ID,
        "customer_id": job.get("customer_id"),
        "title": title,
        "body": body,
        "type": notification_type,
        "read": False,
    }
    try:
        url = f"{REST_URL}/notifications"
        headers = {**SB_HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}
        resp = requests.post(url, headers=headers, json=notification)
        resp.raise_for_status()
    except Exception as e:
        print(f"Notification insert failed (non-fatal): {e}")


def process_upload(job):
    job_id = job["id"]
    input_path = job.get("input_file_path")
    if not input_path:
        raise ValueError("input_file_path missing on job")

    file_bytes = download_file(UPLOADS_BUCKET, input_path)

    import processor

    records = processor.process_file(file_bytes, source_name=input_path)

    customer_id = job.get("customer_id")
    if not customer_id:
        raise ValueError("customer_id missing on job")

    # Reprocessing a file replaces its records instead of appending.
    # Skipped when nothing parsed, so a bad parse cannot wipe good rows.
    if records:
        delete_records_for_source(input_path)

    for r in records:
        record = {
            "product_id": PRODUCT_ID,
            "customer_id": customer_id,
            "title": r["title"],
            "status": r["status"],
            "details": r["details"],
            "source_file_path": input_path,
            "due_date": r.get("due_date"),
        }
        insert_record(record)

    result_data = json.dumps({
        "processed": len(records),
        "input_file_path": input_path,
    }).encode('utf-8')

    result_path = upload_file(RESULTS_BUCKET, f"{job_id}.json", result_data)

    update_job(
        job_id,
        "completed",
        output_file_path=result_path,
        result_summary=f"Processed {len(records)} records",
    )
    insert_notification(job, success=True)


def fail_job(job, error_message):
    print(f"Job {job.get('id')} failed: {error_message}")
    traceback.print_exc()
    try:
        update_job(job["id"], "failed", result_summary=error_message[:500])
        insert_notification(job, success=False)
    except Exception as e:
        print(f"Could not update job to failed: {e}")


def poll():
    while True:
        try:
            jobs = get_pending_jobs()
            for job in jobs:
                try:
                    process_upload(job)
                except Exception as e:
                    fail_job(job, str(e))
        except Exception as e:
            print(f"Poller outer error: {e}")
            traceback.print_exc()
        time.sleep(60)


if __name__ == "__main__":
    print("Poller started")
    poll()

"""
document_storage.py — Uploads document HTML to Supabase Storage

Stores rendered HTML for proposals and invoices in a 'documents' bucket.
File path: {client_id}/{doc_type}/{document_id}.html

Requires a public bucket named 'documents' in Supabase Storage.
Create it: Supabase dashboard → Storage → New bucket → name: documents → Public: on

Usage:
    from execution.document_storage import upload_document_html
    url = upload_document_html(document_id, client_id, 'proposal', html_content)
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def upload_document_html(
    document_id: str,
    client_id: str,
    doc_type: str,
    html_content: str,
) -> str | None:
    """
    Upload a document HTML file to Supabase Storage.

    Args:
        document_id: UUID of the proposal or invoice
        client_id:   UUID of the client (used in file path)
        doc_type:    'proposal' or 'invoice'
        html_content: Full HTML string to upload

    Returns:
        Public URL string, or None if upload failed.
    """
    try:
        from supabase import create_client

        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            print(f"[{timestamp()}] ERROR document_storage: Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
            return None

        client = create_client(url, key)

        # File path: {client_id}/{doc_type}/{document_id}.html
        file_path = f"{client_id}/{doc_type}/{document_id}.html"

        # Try to remove existing file first (upsert pattern)
        try:
            client.storage.from_("documents").remove([file_path])
        except Exception:
            pass  # File may not exist yet — that's fine

        # Upload to the 'documents' bucket. content-type ensures the
        # browser renders the HTML instead of showing raw source;
        # cache-control no-cache forces a fresh fetch every time so
        # owner edits land immediately for the customer.
        client.storage.from_("documents").upload(
            path=file_path,
            file=html_content.encode("utf-8"),
            file_options={
                "content-type": "text/html; charset=utf-8",
                "cache-control": "no-cache",
            },
        )

        # Build the public URL
        public_url = f"{url}/storage/v1/object/public/documents/{file_path}"
        print(f"[{timestamp()}] INFO document_storage: Uploaded {doc_type} → {public_url}")
        return public_url

    except Exception as e:
        print(f"[{timestamp()}] ERROR document_storage: Upload failed — {e}")
        return None

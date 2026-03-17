"""
proposal_storage.py — Uploads proposal HTML to Supabase Storage, returns public URL

Requires a public bucket named 'proposals' in Supabase Storage.
Create it: Supabase dashboard → Storage → New bucket → name: proposals → Public: on
"""
import os
import uuid
from datetime import datetime

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def upload_proposal_html(html_content: str, customer_name: str) -> str | None:
    """
    Upload a proposal HTML file to Supabase Storage.
    Returns the public URL, or None on failure.

    Args:
        html_content:  Full HTML string to upload
        customer_name: Used to build a readable filename

    Returns:
        Public URL string, or None if upload failed.
    """
    try:
        from supabase import create_client

        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            print("[proposal_storage] ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
            return None

        client = create_client(url, key)

        # Build a unique filename
        date_str  = datetime.now().strftime("%Y%m%d")
        safe_name = customer_name.replace(" ", "_").lower()
        filename  = f"{date_str}_{safe_name}_{uuid.uuid4().hex[:8]}.html"

        # Upload to the 'proposals' bucket
        client.storage.from_("proposals").upload(
            path=filename,
            file=html_content.encode("utf-8"),
            file_options={"contentType": "text/html; charset=utf-8"},
        )

        # Build the public URL
        public_url = f"{url}/storage/v1/object/public/proposals/{filename}"
        print(f"[proposal_storage] INFO: Uploaded proposal → {public_url}")
        return public_url

    except Exception as e:
        print(f"[proposal_storage] ERROR: Upload failed — {e}")
        return None

"""
document_routes.py — Flask Blueprint for document edit/view/send routes

Blueprint: document_bp, prefix '/doc'

Routes:
  GET  /doc/edit/<edit_token>?type=proposal  — Render editable document page
  POST /doc/save                             — Save edited line items/notes
  POST /doc/send                             — Send document to customer via SMS

All errors are caught and logged — never crashes the webhook server.
"""

import os
import sys
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify, make_response

document_bp = Blueprint("document_bp", __name__, url_prefix="/doc")


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# GET /doc/edit/<edit_token>?type=proposal
# ---------------------------------------------------------------------------

@document_bp.route("/edit/<edit_token>")
def edit_document(edit_token):
    """Serve the editable document page for a proposal or invoice."""
    try:
        from execution.db_document import (
            get_document_by_token, get_client_by_id, get_customer_by_id,
        )
        from execution.document_html import build_document_html

        doc_type = request.args.get("type", "proposal")
        if doc_type not in ("proposal", "invoice"):
            doc_type = "proposal"

        # Look up the document
        document = get_document_by_token(edit_token, doc_type)
        if not document:
            return _error_page("Document not found", "We couldn't find this document. It may have been removed or the link is incorrect."), 404

        # Load client and customer
        client = get_client_by_id(document.get("client_id")) if document.get("client_id") else None
        customer = get_customer_by_id(document.get("customer_id")) if document.get("customer_id") else None

        # Build the editable HTML page
        html = build_document_html(
            document=document,
            client=client,
            customer=customer,
            doc_type=doc_type,
            edit_mode=True,
        )

        response = make_response(html)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response

    except Exception as e:
        print(f"[{timestamp()}] ERROR document_routes: edit_document failed — {e}")
        return _error_page("Something went wrong", "Please try again or contact support."), 500


# ---------------------------------------------------------------------------
# POST /doc/save
# ---------------------------------------------------------------------------

@document_bp.route("/save", methods=["POST"])
def save_document():
    """
    Save edited document fields.

    Expected JSON: {edit_token, doc_type, line_items, tax_rate, notes}

    Steps:
      1. Look up document by edit_token + doc_type
      2. Capture original values for diff
      3. Compute subtotal/tax/total from line_items + tax_rate
      4. Diff and log each changed field to estimate_edits
      5. Update document in DB
      6. Rebuild view HTML, upload to storage, update html_url
      7. Trigger learning loop
      8. Return {success: true, html_url}
    """
    try:
        from execution.db_document import (
            get_document_by_token, get_client_by_id, get_customer_by_id,
            update_proposal_fields, update_invoice_fields,
            log_edit, get_recent_edits, upsert_prompt_override,
        )
        from execution.document_html import build_document_html
        from execution.document_storage import upload_document_html

        data = request.get_json(force=True, silent=True) or {}
        edit_token = data.get("edit_token", "")
        doc_type = data.get("doc_type", "proposal")
        line_items = data.get("line_items", [])
        tax_rate = float(data.get("tax_rate", 0))
        notes = data.get("notes", "")

        if not edit_token:
            return jsonify({"success": False, "error": "Missing edit_token"}), 400

        # Step 1: Look up document
        document = get_document_by_token(edit_token, doc_type)
        if not document:
            return jsonify({"success": False, "error": "Document not found"}), 404

        doc_id = document["id"]
        client_id = document.get("client_id", "")

        # Step 2: Capture originals for diffing
        original_line_items = document.get("line_items") or []
        if doc_type == "proposal":
            original_notes = document.get("proposal_text", "") or ""
            original_total = float(document.get("amount_estimate") or 0)
        else:
            original_notes = document.get("invoice_text", "") or ""
            original_total = float(document.get("amount_due") or 0)

        # Step 3: Compute totals
        subtotal = sum(float(item.get("total", 0)) for item in line_items)
        tax_amount = round(subtotal * tax_rate, 2)
        total = round(subtotal + tax_amount, 2)

        # Step 4: Diff and log changes
        if str(original_line_items) != str(line_items):
            log_edit(doc_type, doc_id, client_id, "line_items",
                     json.dumps(original_line_items) if original_line_items else "[]",
                     json.dumps(line_items))

        if original_notes.strip() != notes.strip():
            log_edit(doc_type, doc_id, client_id, "notes", original_notes, notes)

        if abs(original_total - total) > 0.01:
            log_edit(doc_type, doc_id, client_id, "total",
                     str(original_total), str(total))

        # Step 5: Update document in DB
        if doc_type == "proposal":
            update_proposal_fields(doc_id, line_items, subtotal, tax_rate, tax_amount, total, notes)
        else:
            update_invoice_fields(doc_id, line_items, subtotal, tax_rate, tax_amount, total, notes)

        # Step 6: Rebuild view HTML, upload to storage
        html_url = None
        try:
            # Refresh document after update
            document_updated = get_document_by_token(edit_token, doc_type)
            client = get_client_by_id(client_id) if client_id else None
            customer_id = document.get("customer_id")
            customer = get_customer_by_id(customer_id) if customer_id else None

            view_html = build_document_html(
                document=document_updated or document,
                client=client,
                customer=customer,
                doc_type=doc_type,
                edit_mode=False,
            )
            html_url = upload_document_html(doc_id, client_id, doc_type, view_html)

            # Update html_url in DB
            if html_url:
                if doc_type == "proposal":
                    update_proposal_fields(doc_id, line_items, subtotal, tax_rate, tax_amount, total, notes, html_url)
                else:
                    update_invoice_fields(doc_id, line_items, subtotal, tax_rate, tax_amount, total, notes, html_url)
        except Exception as e:
            print(f"[{timestamp()}] WARN document_routes: HTML upload failed — {e}")

        # Step 7: Trigger learning update
        try:
            _run_learning_update(client_id, doc_type)
        except Exception as e:
            print(f"[{timestamp()}] WARN document_routes: Learning update failed — {e}")

        print(f"[{timestamp()}] INFO document_routes: Saved {doc_type} {doc_id} total=${total}")
        return jsonify({"success": True, "html_url": html_url})

    except Exception as e:
        print(f"[{timestamp()}] ERROR document_routes: save_document failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /doc/send
# ---------------------------------------------------------------------------

@document_bp.route("/send", methods=["POST"])
def send_document():
    """
    Send the document to the customer via SMS.

    Expected JSON: {edit_token, doc_type}

    Steps:
      1. Look up document, client, customer
      2. Build view HTML, upload to storage
      3. Update status + sent_at + html_url
      4. SMS customer with the link
      5. SMS owner with confirmation
    """
    try:
        from execution.db_document import (
            get_document_by_token, get_client_by_id, get_customer_by_id,
        )
        from execution.db_connection import get_client as get_supabase
        from execution.document_html import build_document_html
        from execution.document_storage import upload_document_html
        from execution.sms_send import send_sms

        data = request.get_json(force=True, silent=True) or {}
        edit_token = data.get("edit_token", "")
        doc_type = data.get("doc_type", "proposal")

        if not edit_token:
            return jsonify({"success": False, "error": "Missing edit_token"}), 400

        # Step 1: Look up everything
        document = get_document_by_token(edit_token, doc_type)
        if not document:
            return jsonify({"success": False, "error": "Document not found"}), 404

        doc_id = document["id"]
        client_id = document.get("client_id", "")
        customer_id = document.get("customer_id", "")

        client = get_client_by_id(client_id) if client_id else None
        customer = get_customer_by_id(customer_id) if customer_id else None

        if not client:
            return jsonify({"success": False, "error": "Client not found"}), 404

        business_name = client.get("business_name", "Your service provider")
        client_phone = client.get("phone", "")
        owner_mobile = client.get("owner_mobile") or client_phone
        customer_name = customer.get("customer_name", "Customer") if customer else "Customer"
        customer_phone = customer.get("customer_phone", "") if customer else ""

        if not customer_phone:
            return jsonify({"success": False, "error": "No customer phone number on file"}), 400

        # Step 2: Build view HTML and upload
        view_html = build_document_html(
            document=document,
            client=client,
            customer=customer,
            doc_type=doc_type,
            edit_mode=False,
        )
        html_url = upload_document_html(doc_id, client_id, doc_type, view_html)

        if not html_url:
            return jsonify({"success": False, "error": "Failed to upload document"}), 500

        # Step 3: Update status in DB
        supabase = get_supabase()
        now = datetime.now(timezone.utc).isoformat()
        table = "proposals" if doc_type == "proposal" else "invoices"

        supabase.table(table).update({
            "status": "sent",
            "sent_at": now,
            "html_url": html_url,
        }).eq("id", doc_id).execute()

        # Step 4: SMS the customer
        base_url = request.host_url.rstrip("/")

        if doc_type == "proposal":
            total = float(document.get("amount_estimate") or 0)
            customer_msg = (
                f"{business_name} sent you an estimate for ${total:.2f}. "
                f"View here: {html_url} — Reply YES to approve or NO to decline."
            )
        else:
            total = float(document.get("amount_due") or 0)

            # For invoices: create Square payment link + token URL with Pay Now
            invoice_view_url = html_url  # fallback to storage URL
            try:
                from execution.square_agent import create_payment_link
                from execution.token_generator import generate_token, attach_payment_link

                amount_cents = int(round(total * 100))
                job_id = document.get("job_id", "")
                description = f"Invoice from {business_name}"

                # Create Square payment link
                square_result = create_payment_link(
                    invoice_id=doc_id,
                    amount_cents=amount_cents,
                    description=description,
                    customer_name=customer_name,
                )

                payment_link_url = None
                square_order_id = None
                square_payment_link_id = None

                if square_result.get("success"):
                    payment_link_url = square_result["payment_link_url"]
                    square_order_id = square_result.get("square_order_id")
                    square_payment_link_id = square_result.get("square_payment_link_id")
                    print(f"[{timestamp()}] INFO document_routes: Square payment link → {payment_link_url}")
                else:
                    print(f"[{timestamp()}] WARN document_routes: Square link failed — {square_result.get('error')} — invoice sent without Pay Now")

                # Generate token for the /i/<token> URL
                token = generate_token(
                    job_id=job_id,
                    client_phone=client_phone,
                    link_type="invoice",
                )
                if token:
                    # Attach Square payment link to the token record
                    if payment_link_url:
                        attach_payment_link(token, payment_link_url, square_order_id, square_payment_link_id)
                    invoice_view_url = f"{base_url}/i/{token}"
                    print(f"[{timestamp()}] INFO document_routes: Invoice view URL → {invoice_view_url}")

            except Exception as e:
                print(f"[{timestamp()}] WARN document_routes: Square/token setup failed — {e} — using storage URL")

            customer_msg = (
                f"{business_name} sent you an invoice for ${total:.2f}. "
                f"View and pay here: {invoice_view_url}"
            )

        sms_result = send_sms(
            to_number=customer_phone,
            message_body=customer_msg,
            from_number=client_phone,
        )

        if not sms_result["success"]:
            print(f"[{timestamp()}] ERROR document_routes: Customer SMS failed — {sms_result['error']}")

        # Step 5: SMS owner confirmation
        doc_label = "Estimate" if doc_type == "proposal" else "Invoice"
        owner_msg = f"{doc_label} sent to {customer_name} for ${total:.2f}"
        send_sms(to_number=owner_mobile, message_body=owner_msg, from_number=client_phone)

        # Log activity
        try:
            from execution.db_agent_activity import log_activity
            log_activity(
                client_phone=client_phone,
                agent_name="document_routes",
                action_taken=f"{doc_type}_sent_to_customer",
                input_summary=f"doc_id={doc_id}",
                output_summary=f"Sent to {customer_name} — ${total:.2f}",
                sms_sent=True,
            )
        except Exception:
            pass

        print(f"[{timestamp()}] INFO document_routes: Sent {doc_type} {doc_id} to {customer_phone}")
        return jsonify({"success": True})

    except Exception as e:
        print(f"[{timestamp()}] ERROR document_routes: send_document failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Learning loop — called after each save
# ---------------------------------------------------------------------------

def _run_learning_update(client_id: str, doc_type: str) -> None:
    """
    Analyze recent edits and update the client's prompt overrides.
    Only runs if there are 2+ edits for this doc_type.
    """
    from execution.db_document import get_recent_edits, upsert_prompt_override
    from execution.call_claude import call_claude

    edits = get_recent_edits(client_id, doc_type, limit=10)
    if len(edits) < 2:
        print(f"[{timestamp()}] INFO document_routes: Only {len(edits)} edits — skipping learning update")
        return

    # Format edits for Claude
    edit_lines = []
    for edit in edits:
        field = edit.get("field_changed", "unknown")
        orig = edit.get("original_value", "")[:200]
        new = edit.get("new_value", "")[:200]
        edit_lines.append(f"Changed {field} from \"{orig}\" to \"{new}\"")

    edits_text = "\n".join(edit_lines)

    system_prompt = "You are a style analyzer for a trade business AI platform."
    user_prompt = (
        f"A business owner has been editing their {doc_type}s. "
        f"Here are their recent edits:\n\n{edits_text}\n\n"
        f"In 2-3 sentences, summarize what this owner prefers. "
        f"Focus on: pricing style, level of detail, tone of notes, "
        f"tax preferences. Be specific and actionable.\n"
        f"Respond with only the summary, no preamble."
    )

    style_notes = call_claude(system_prompt, user_prompt, model="haiku")
    if style_notes:
        upsert_prompt_override(client_id, doc_type, style_notes)
        print(f"[{timestamp()}] INFO document_routes: Updated style notes for client {client_id[:8]}...")
    else:
        print(f"[{timestamp()}] WARN document_routes: Claude returned no style analysis")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error_page(title: str, message: str) -> str:
    """Return a simple branded error page."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: system-ui, -apple-system, sans-serif;
      background: #f4f5f7;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 1rem;
    }}
    .card {{
      max-width: 440px;
      width: 100%;
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 2px 16px rgba(0,0,0,0.08);
      text-align: center;
      overflow: hidden;
    }}
    .card-header {{
      background: #1a2744;
      color: #fff;
      padding: 2rem 1.5rem;
    }}
    .card-header h1 {{ font-size: 1.2rem; font-weight: 600; }}
    .card-body {{
      padding: 2rem 1.5rem;
      color: #555;
      font-size: 0.95rem;
      line-height: 1.6;
    }}
    .card-footer {{
      padding: 0.75rem;
      font-size: 0.75rem;
      color: #94a3b8;
      border-top: 1px solid #e5e7eb;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="card-header"><h1>{title}</h1></div>
    <div class="card-body"><p>{message}</p></div>
    <div class="card-footer">Powered by Bolts11</div>
  </div>
</body>
</html>"""

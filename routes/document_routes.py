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

from flask import Blueprint, request, jsonify, make_response, send_from_directory

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

        # Step 3: Compute totals — tax only on items marked taxable
        subtotal = sum(float(item.get("total", 0)) for item in line_items)
        taxable_subtotal = sum(float(item.get("total", 0)) for item in line_items if item.get("taxable"))
        tax_amount = round(taxable_subtotal * tax_rate, 2)
        total = round(subtotal + tax_amount, 2)

        # Step 4: Diff and log changes
        if str(original_line_items) != str(line_items):
            log_edit(doc_type, doc_id, client_id, "line_items",
                     json.dumps(original_line_items) if original_line_items else "[]",
                     json.dumps(line_items))

            # Log individual price adjustments for pricebook learning
            try:
                from execution.db_pricing import log_price_adjustment
                # Build lookup of original prices by description
                orig_prices = {}
                for li in (original_line_items or []):
                    desc = (li.get("description") or "").strip().lower()
                    price = float(li.get("total") or li.get("amount") or 0)
                    if desc and price:
                        orig_prices[desc] = price

                # Compare each new line item against originals
                client_record = get_client_by_id(client_id) if client_id else None
                vertical = (client_record or {}).get("trade_vertical", "")
                for li in line_items:
                    desc = (li.get("description") or "").strip().lower()
                    new_price = float(li.get("total") or li.get("amount") or 0)
                    if desc in orig_prices and abs(orig_prices[desc] - new_price) > 0.01:
                        log_price_adjustment(
                            client_id=client_id,
                            vertical_key=vertical,
                            service_name=li.get("description", "").strip(),
                            original_price=orig_prices[desc],
                            adjusted_price=new_price,
                            context=f"{doc_type}_edit",
                        )
            except Exception as pa_err:
                print(f"[doc_save] WARN: price adjustment logging failed — {pa_err}")

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

        # Step 6b: Regenerate Square payment link if invoice total changed
        if doc_type == "invoice" and abs(original_total - total) > 0.01 and total > 0:
            try:
                import os as _os
                if _os.environ.get("SQUARE_ACCESS_TOKEN"):
                    from execution.token_generator import generate_token, attach_payment_link
                    from execution.square_agent import create_payment_link

                    # Get client_phone for token generation
                    client_record = get_client_by_id(client_id) if client_id else None
                    client_phone = (client_record or {}).get("phone", "")

                    if client_phone:
                        job_id = document.get("job_id", "")
                        invoice_token = generate_token(
                            job_id=job_id or doc_id,
                            client_phone=client_phone,
                            link_type="invoice",
                        )
                        if invoice_token:
                            amount_cents = int(round(total * 100))
                            print(f"[{timestamp()}] INFO document_routes: Square regen total=${total:.2f} → {amount_cents}¢")
                            customer_name = (get_customer_by_id(document.get("customer_id")) or {}).get("customer_name", "Customer")
                            biz_name = (client_record or {}).get("business_name", "")
                            square_result = create_payment_link(
                                invoice_id=doc_id,
                                amount_cents=amount_cents,
                                description=f"Invoice — {biz_name}",
                                customer_name=customer_name,
                            )
                            if square_result.get("success"):
                                attach_payment_link(
                                    token=invoice_token,
                                    payment_link_url=square_result["payment_link_url"],
                                    square_order_id=square_result.get("square_order_id"),
                                    square_payment_link_id=square_result.get("square_payment_link_id"),
                                )
                                # Update payment_link_url on invoice
                                from execution.db_connection import get_client as _get_sb2
                                _get_sb2().table("invoices").update({
                                    "payment_link_url": square_result["payment_link_url"],
                                }).eq("id", doc_id).execute()
                                print(f"[{timestamp()}] INFO document_routes: Square link regenerated for ${total:.2f} → {square_result['payment_link_url']}")
                            else:
                                print(f"[{timestamp()}] WARN document_routes: Square link regen failed — {square_result.get('error')}")
            except Exception as e:
                print(f"[{timestamp()}] WARN document_routes: Square link regeneration error — {e}")

        # Step 7: Trigger learning update (style preferences)
        try:
            _run_learning_update(client_id, doc_type)
        except Exception as e:
            print(f"[{timestamp()}] WARN document_routes: Learning update failed — {e}")

        # Step 7b: Trigger pricebook learning (auto-update prices from consistent edits)
        try:
            from execution.db_pricebook import learn_from_adjustments
            learn_result = learn_from_adjustments(client_id)
            if learn_result.get("services_updated"):
                print(f"[{timestamp()}] INFO document_routes: Pricebook learned {learn_result['services_updated']} price updates")
        except Exception as e:
            print(f"[{timestamp()}] WARN document_routes: Pricebook learning failed — {e}")

        print(f"[{timestamp()}] INFO document_routes: Saved {doc_type} {doc_id} total=${total}")
        return jsonify({"success": True, "html_url": html_url})

    except Exception as e:
        print(f"[{timestamp()}] ERROR document_routes: save_document failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# GET /doc/review/<edit_token> — Mobile review screen (no login required)
# ---------------------------------------------------------------------------

@document_bp.route("/review/<edit_token>")
def review_document(edit_token):
    """Serve the mobile-first review page for approving/rejecting a draft."""
    doc_type = request.args.get("type", "proposal")
    return send_from_directory(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard"),
        "review.html"
    )


# ---------------------------------------------------------------------------
# GET /doc/review/<edit_token>/data — Load document data for review page
# ---------------------------------------------------------------------------

@document_bp.route("/review/<edit_token>/data", methods=["GET"])
def review_document_data(edit_token):
    """Return document data for the mobile review screen."""
    try:
        from execution.db_document import (
            get_document_by_token, get_client_by_id, get_customer_by_id,
        )
        doc_type = request.args.get("type", "proposal")
        document = get_document_by_token(edit_token, doc_type)
        if not document:
            return jsonify({"success": False, "error": "Document not found"}), 404

        client = get_client_by_id(document.get("client_id")) if document.get("client_id") else None
        customer = get_customer_by_id(document.get("customer_id")) if document.get("customer_id") else None

        # Parse line items
        line_items = document.get("line_items") or []
        if isinstance(line_items, str):
            line_items = json.loads(line_items)

        return jsonify({
            "success": True,
            "doc_type": doc_type,
            "edit_token": edit_token,
            "document": {
                "id": document["id"],
                "status": document.get("status", "draft"),
                "reviewed_at": document.get("reviewed_at"),
                "line_items": line_items,
                "notes": document.get("proposal_text" if doc_type == "proposal" else "invoice_text", ""),
                "amount": float(document.get("amount_estimate" if doc_type == "proposal" else "amount_due") or 0),
                "tax_rate": float(document.get("tax_rate") or 0),
                "created_at": document.get("created_at"),
            },
            "customer": {
                "name": (customer or {}).get("customer_name", ""),
                "phone": (customer or {}).get("customer_phone", ""),
                "address": (customer or {}).get("customer_address", ""),
                "email": (customer or {}).get("customer_email", ""),
            },
            "business": {
                "name": (client or {}).get("business_name", ""),
            },
        })
    except Exception as e:
        print(f"[{timestamp()}] ERROR document_routes: review_document_data — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /doc/approve — Approve a draft (with corrections logged)
# ---------------------------------------------------------------------------

@document_bp.route("/approve", methods=["POST"])
def approve_document():
    """
    Approve a reviewed document. Logs corrections as training signals.

    Expected JSON: {
        edit_token, doc_type,
        line_items: [...],      // corrected line items
        customer_name: "...",   // corrected if needed
        customer_address: "...",
        notes: "..."
    }
    """
    try:
        from execution.db_document import (
            get_document_by_token, get_client_by_id,
            update_proposal_fields, update_invoice_fields,
        )
        from execution.db_connection import get_client as get_supabase

        data = request.get_json(force=True, silent=True) or {}
        edit_token = data.get("edit_token", "")
        doc_type = data.get("doc_type", "proposal")
        corrected_items = data.get("line_items")
        corrected_notes = data.get("notes")

        if not edit_token:
            return jsonify({"success": False, "error": "Missing edit_token"}), 400

        document = get_document_by_token(edit_token, doc_type)
        if not document:
            return jsonify({"success": False, "error": "Document not found"}), 404

        doc_id = document["id"]
        client_id = document.get("client_id", "")
        job_id = document.get("job_id")
        sb = get_supabase()
        now = datetime.now(timezone.utc).isoformat()

        # Get job context for training signals
        job_type = ""
        if job_id:
            try:
                jr = sb.table("jobs").select("job_type").eq("id", job_id).execute()
                if jr.data:
                    job_type = jr.data[0].get("job_type", "")
            except Exception:
                pass

        # Log corrections as training signals
        original_items = document.get("line_items") or []
        if isinstance(original_items, str):
            original_items = json.loads(original_items)

        if corrected_items is not None:
            _log_draft_corrections(
                sb, client_id, doc_type, doc_id, job_id, job_type,
                original_items, corrected_items, corrected_notes,
                document.get("proposal_text" if doc_type == "proposal" else "invoice_text", ""),
            )

            # Apply corrections to the document
            subtotal = sum(float(li.get("total") or li.get("amount") or 0) for li in corrected_items)
            tax_rate = float(document.get("tax_rate") or 0)
            tax_amount = round(subtotal * tax_rate, 2)
            total = round(subtotal + tax_amount, 2)
            notes = corrected_notes if corrected_notes is not None else ""

            if doc_type == "proposal":
                update_proposal_fields(doc_id, corrected_items, subtotal, tax_rate, tax_amount, total, notes)
            else:
                update_invoice_fields(doc_id, corrected_items, subtotal, tax_rate, tax_amount, total, notes)

        # Mark as reviewed
        table = "proposals" if doc_type == "proposal" else "invoices"
        sb.table(table).update({
            "reviewed_at": now,
            "reviewed_by": client_id,
        }).eq("id", doc_id).execute()

        print(f"[{timestamp()}] INFO document_routes: {doc_type} {doc_id[:8]} approved")
        return jsonify({"success": True})

    except Exception as e:
        print(f"[{timestamp()}] ERROR document_routes: approve_document — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /doc/reject — Reject a draft with a reason
# ---------------------------------------------------------------------------

@document_bp.route("/reject", methods=["POST"])
def reject_document():
    """Reject a draft — logs the reason as a negative training signal."""
    try:
        from execution.db_document import get_document_by_token
        from execution.db_connection import get_client as get_supabase

        data = request.get_json(force=True, silent=True) or {}
        edit_token = data.get("edit_token", "")
        doc_type = data.get("doc_type", "proposal")
        reason = data.get("reason", "").strip()

        document = get_document_by_token(edit_token, doc_type)
        if not document:
            return jsonify({"success": False, "error": "Document not found"}), 404

        doc_id = document["id"]
        client_id = document.get("client_id", "")
        sb = get_supabase()
        now = datetime.now(timezone.utc).isoformat()

        table = "proposals" if doc_type == "proposal" else "invoices"
        sb.table(table).update({
            "rejected_at": now,
            "rejection_reason": reason or "No reason given",
            "status": "rejected",
        }).eq("id", doc_id).execute()

        # Log rejection as a training signal
        try:
            sb.table("draft_corrections").insert({
                "client_id": client_id,
                "document_type": doc_type,
                "document_id": doc_id,
                "job_id": document.get("job_id"),
                "field_name": "document",
                "ai_value": "full draft",
                "owner_value": reason or "rejected",
                "action": "reject",
            }).execute()
        except Exception:
            pass

        print(f"[{timestamp()}] INFO document_routes: {doc_type} {doc_id[:8]} rejected — {reason}")
        return jsonify({"success": True})

    except Exception as e:
        print(f"[{timestamp()}] ERROR document_routes: reject_document — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _log_draft_corrections(sb, client_id, doc_type, doc_id, job_id, job_type,
                           original_items, corrected_items, corrected_notes, original_notes):
    """Compare AI draft against owner's corrections and log each change."""
    try:
        # Build lookup of original items by index
        for i, corrected in enumerate(corrected_items):
            original = original_items[i] if i < len(original_items) else None

            if original is None:
                # New item added by owner
                sb.table("draft_corrections").insert({
                    "client_id": client_id,
                    "document_type": doc_type,
                    "document_id": doc_id,
                    "job_id": job_id,
                    "job_type": job_type,
                    "field_name": "line_item",
                    "ai_value": None,
                    "owner_value": json.dumps(corrected),
                    "action": "add",
                }).execute()
                continue

            # Check description change
            orig_desc = (original.get("description") or "").strip()
            corr_desc = (corrected.get("description") or "").strip()
            if orig_desc != corr_desc and corr_desc:
                sb.table("draft_corrections").insert({
                    "client_id": client_id,
                    "document_type": doc_type,
                    "document_id": doc_id,
                    "job_id": job_id,
                    "job_type": job_type,
                    "field_name": "description",
                    "ai_value": orig_desc,
                    "owner_value": corr_desc,
                    "action": "edit",
                }).execute()

            # Check price change
            orig_price = float(original.get("total") or original.get("amount") or 0)
            corr_price = float(corrected.get("total") or corrected.get("amount") or 0)
            if abs(orig_price - corr_price) > 0.01:
                sb.table("draft_corrections").insert({
                    "client_id": client_id,
                    "document_type": doc_type,
                    "document_id": doc_id,
                    "job_id": job_id,
                    "job_type": job_type,
                    "field_name": "price",
                    "ai_value": str(orig_price),
                    "owner_value": str(corr_price),
                    "action": "edit",
                }).execute()

        # Check for removed items
        if len(original_items) > len(corrected_items):
            for i in range(len(corrected_items), len(original_items)):
                sb.table("draft_corrections").insert({
                    "client_id": client_id,
                    "document_type": doc_type,
                    "document_id": doc_id,
                    "job_id": job_id,
                    "job_type": job_type,
                    "field_name": "line_item",
                    "ai_value": json.dumps(original_items[i]),
                    "owner_value": None,
                    "action": "remove",
                }).execute()

        # Check notes change
        if corrected_notes is not None and (original_notes or "").strip() != corrected_notes.strip():
            sb.table("draft_corrections").insert({
                "client_id": client_id,
                "document_type": doc_type,
                "document_id": doc_id,
                "job_id": job_id,
                "job_type": job_type,
                "field_name": "notes",
                "ai_value": (original_notes or "").strip()[:500],
                "owner_value": corrected_notes.strip()[:500],
                "action": "edit",
            }).execute()

    except Exception as e:
        print(f"[{timestamp()}] WARN document_routes: _log_draft_corrections failed — {e}")


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
        proposal_view_url = None  # set in the proposal branch below

        if doc_type == "proposal":
            total = float(document.get("amount_estimate") or 0)

            # Mint a public token and route the customer to the Flask
            # /p/<token> view route. That route renders proposal.html
            # server-side, which guarantees correct content-type and
            # gives us a real web page (not raw HTML rendered from a
            # Storage URL whose content-type header may have been
            # dropped). Mirrors the /i/<token> path used for invoices.
            try:
                from execution.token_generator import generate_token
                proposal_token = generate_token(
                    job_id=document.get("job_id") or doc_id,
                    client_phone=client_phone,
                    link_type="proposal",
                )
                if proposal_token:
                    proposal_view_url = f"{base_url}/p/{proposal_token}"
                    print(f"[{timestamp()}] INFO document_routes: Proposal view URL → {proposal_view_url}")
                else:
                    print(f"[{timestamp()}] WARN document_routes: proposal token mint returned None — falling back to storage URL")
            except Exception as e:
                print(f"[{timestamp()}] WARN document_routes: proposal token mint failed — {e} — falling back to storage URL")
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

        # Step 4b: Deliver to customer via notify router (email or SMS based on switches)
        from execution.notify import notify_document, notify

        # Determine the view URL and pay URL.
        #
        # Proposals: prefer the freshly minted /p/<token> Flask route
        # (server-rendered, guaranteed text/html). Fall back to the
        # Storage URL only if token mint failed, and the owner-edit
        # page only if both are unavailable.
        # Invoices: same idea but /i/<token>.
        if doc_type == "invoice":
            view_url = invoice_view_url
        else:
            view_url = (
                proposal_view_url
                or html_url
                or f"{base_url}/doc/edit/{edit_token}?type={doc_type}"
            )
        pay_url = payment_link_url if doc_type == "invoice" else None
        customer_email = customer.get("customer_email", "") if customer else ""

        delivery_result = notify_document(
            client_id=client_id,
            to_phone=customer_phone,
            doc_type=doc_type,
            doc_id=doc_id,
            amount=total,
            customer_name=customer_name,
            view_url=view_url,
            pay_url=pay_url,
            to_email=customer_email,
        )

        if not delivery_result["success"]:
            print(f"[{timestamp()}] ERROR document_routes: Customer delivery failed — {delivery_result['error']}")
        else:
            print(f"[{timestamp()}] INFO document_routes: Delivered to customer via {delivery_result['channel']}")

        # Step 4c: Schedule the 3-day estimate follow-up — proposals only,
        # and ONLY when delivery to the customer actually succeeded.
        # This used to fire at draft time inside proposal_agent.run(), which
        # caused the followup cron to message customers before the owner had
        # ever approved the draft. Moving it here ties the followup timer to
        # the real moment the customer received the estimate.
        if doc_type == "proposal" and delivery_result.get("success"):
            try:
                from execution.db_followups import schedule_followup
                from datetime import timedelta
                follow_up_time = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
                schedule_followup(
                    client_id=client_id,
                    customer_id=document.get("customer_id"),
                    job_id=document.get("job_id"),
                    proposal_id=doc_id,
                    followup_type="estimate_followup",
                    scheduled_for=follow_up_time,
                )
                print(f"[{timestamp()}] INFO document_routes: estimate_followup scheduled for {follow_up_time}")
            except Exception as e:
                print(f"[{timestamp()}] WARN document_routes: schedule_followup failed — {e}")

        # Step 5: Notify owner (internal — uses notify router too)
        doc_label = "Estimate" if doc_type == "proposal" else "Invoice"
        owner_msg = f"{doc_label} sent to {customer_name} for ${total:.2f} (via {delivery_result.get('channel', 'unknown')})"
        notify(client_id=client_id, to_phone=owner_mobile, message=owner_msg, message_type="confirmation")

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

     # Step 6: Write pricing history — proposals only, after successful delivery.
        # Powers the "last 3 averaged $X" reference in the guided estimate flow.
        # Non-fatal — send still succeeds if this fails.
        if doc_type == "proposal" and delivery_result.get("success"):
            try:
                from execution.db_pricing_history import record_sent_proposal
                job_type_val = ""
                if document.get("job_id"):
                    _jr = supabase.table("jobs").select("job_type").eq(
                        "id", document["job_id"]
                    ).execute()
                    if _jr.data:
                        job_type_val = _jr.data[0].get("job_type", "")
                record_sent_proposal(
                    client_id=client_id,
                    customer_id=customer_id or None,
                    job_id=document.get("job_id"),
                    proposal_id=doc_id,
                    job_type=job_type_val,
                    amount=total,
                )
            except Exception as _e:
                print(f"[{timestamp()}] WARN document_routes: pricing history write failed — {_e}")

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

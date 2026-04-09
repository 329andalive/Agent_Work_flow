"""
dispatch_routes.py — Flask Blueprint for dispatch assignment + route sending

Blueprint: dispatch_bp
Routes:
    POST /api/dispatch/assign — assign a single job to a worker
    POST /api/dispatch/send   — snapshot all assignments, generate tokens, SMS workers
    GET  /r/<token>           — worker route page (mobile, no login)
"""

import os
import sys
import uuid
import string
import secrets
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify, session, render_template

dispatch_bp = Blueprint("dispatch_bp", __name__)


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_supabase():
    from execution.db_connection import get_client
    return get_client()


def _resolve_client_id():
    """Same auth pattern as dashboard_routes."""
    from flask import current_app
    if current_app.debug or os.environ.get("FLASK_ENV") == "development":
        cid = session.get("client_id")
        if cid:
            return cid
        try:
            sb = _get_supabase()
            r = sb.table("clients").select("id").eq("active", True).order("created_at").limit(1).execute()
            if r.data:
                return r.data[0]["id"]
        except Exception:
            pass
        return None
    return session.get("client_id")


def _load_client(client_id: str) -> dict:
    """Load client record for phone + business name."""
    try:
        sb = _get_supabase()
        result = sb.table("clients").select("id, business_name, phone, owner_mobile").eq("id", client_id).execute()
        if result.data:
            return result.data[0]
    except Exception:
        pass
    return {"id": client_id, "business_name": "Bolts11", "phone": "", "owner_mobile": ""}


def _generate_route_token() -> str:
    """Generate an 8-char alphanumeric token for route URLs."""
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(8))


# ---------------------------------------------------------------------------
# POST /api/dispatch/assign — single assignment
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/dispatch/assign", methods=["POST"])
def dispatch_assign():
    """
    Assign a single job to a worker. Calls db_scheduling.save_dispatch_session
    for the assignment and updates the job's dispatch columns.
    """
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid JSON body"}), 400

    job_id = data.get("job_id")
    worker_id = data.get("worker_id")
    if not job_id or not worker_id:
        return jsonify({"success": False, "error": "job_id and worker_id required"}), 400

    wave_id = data.get("wave_id")
    sort_order = data.get("sort_order", 0)

    try:
        from execution.db_scheduling import save_dispatch_session
        session_id = save_dispatch_session(
            client_id=client_id,
            assignments=[{
                "job_id": job_id,
                "worker_id": worker_id,
                "wave_id": wave_id,
                "sort_order": sort_order,
            }],
        )

        # Upsert to route_assignments so dragging back and forth stays in sync
        try:
            from datetime import date as date_cls
            dispatch_date = data.get("date", date_cls.today().isoformat())
            sb = _get_supabase()
            existing = sb.table("route_assignments").select("id").eq(
                "job_id", job_id).eq("client_id", client_id).execute()
            if existing.data:
                sb.table("route_assignments").update({
                    "worker_id": worker_id,
                    "dispatch_date": dispatch_date,
                    "assigned_at": datetime.now(timezone.utc).isoformat(),
                }).eq("job_id", job_id).eq("client_id", client_id).execute()
            else:
                sb.table("route_assignments").insert({
                    "client_id": client_id,
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "dispatch_date": dispatch_date,
                    "assigned_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
        except Exception as e:
            print(f"[{timestamp()}] WARN dispatch: assign upsert failed — {e}")

        if session_id:
            print(f"[{timestamp()}] INFO dispatch: Assigned job {job_id[:8]} → worker {worker_id[:8]}")
            return jsonify({"success": True, "assignment_id": session_id})
        else:
            return jsonify({"success": False, "error": "Assignment failed"}), 500

    except Exception as e:
        print(f"[{timestamp()}] ERROR dispatch: assign failed — {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/dispatch/send — snapshot + SMS blast
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/dispatch/send", methods=["POST"])
def dispatch_send():
    """
    Snapshot all assignments for the day, generate route tokens,
    and SMS each worker their route.
    """
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid JSON body"}), 400

    dispatch_date = data.get("date")
    assignments = data.get("assignments", [])
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not dispatch_date:
        return jsonify({"success": False, "error": "date is required"}), 400
    if not assignments:
        return jsonify({"success": False, "error": "No assignments to send"}), 400

    client = _load_client(client_id)
    business_name = client.get("business_name", "Bolts11")
    base_url = os.environ.get("BOLTS11_BASE_URL", "https://bolts11.com")
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()

    sb = _get_supabase()

    # Guard: skip if this session_id was already dispatched (prevents duplicate sends)
    try:
        existing = sb.table("dispatch_decisions").select("id").eq(
            "session_id", session_id
        ).eq("client_id", client_id).limit(1).execute()
        if existing.data:
            print(f"[{timestamp()}] WARN dispatch: session {session_id[:8]} already saved — skipping duplicate write")
            return jsonify({"success": True, "duplicate": True})
    except Exception:
        pass  # Table may not exist yet — proceed normally

    workers_notified = 0
    sms_sent = 0
    sms_failed = []
    total_jobs = 0

    for worker_assignment in assignments:
        worker_id = worker_assignment.get("worker_id")
        jobs = worker_assignment.get("jobs", [])
        if not worker_id or not jobs:
            continue

        total_jobs += len(jobs)
        has_waves = any(j.get("wave_id") for j in jobs)

        # Look up worker from employees table
        worker_phone = None
        worker_name = "Worker"
        try:
            w = sb.table("employees").select("name, phone").eq("id", worker_id).execute()
            if w.data:
                worker_phone = w.data[0].get("phone")
                worker_name = w.data[0].get("name", "Worker")
        except Exception as e:
            print(f"[{timestamp()}] WARN dispatch: employee lookup failed for {worker_id[:8]} — {e}")

        # Save assignments via db_scheduling
        try:
            from execution.db_scheduling import save_dispatch_session
            save_dispatch_session(
                client_id=client_id,
                assignments=[{
                    "job_id": j["job_id"],
                    "worker_id": worker_id,
                    "wave_id": j.get("wave_id"),
                    "sort_order": j.get("sort_order", 0),
                } for j in jobs],
                session_id=session_id,
            )
        except Exception as e:
            print(f"[{timestamp()}] WARN dispatch: save_dispatch_session failed — {e}")

        # Log each assignment to dispatch_decisions for AI learning
        for j in jobs:
            try:
                # Look up job details for the learning record
                job_detail = {}
                try:
                    jr = sb.table("jobs").select("job_type, zone_cluster, requested_time").eq("id", j["job_id"]).execute()
                    if jr.data:
                        job_detail = jr.data[0]
                except Exception:
                    pass

                sb.table("dispatch_decisions").insert({
                    "client_id": client_id,
                    "session_id": session_id,
                    "dispatch_date": dispatch_date,
                    "job_id": j["job_id"],
                    "worker_id": worker_id,
                    "job_type": job_detail.get("job_type"),
                    "zone_cluster": job_detail.get("zone_cluster"),
                    "requested_time": job_detail.get("requested_time"),
                    "sort_order": j.get("sort_order", 0),
                    "was_suggested": j.get("was_suggested", False),
                    "was_accepted": j.get("was_accepted", False),
                    "was_overridden": j.get("was_overridden", False),
                    "override_reason": j.get("override_reason"),
                }).execute()
            except Exception as e:
                print(f"[{timestamp()}] WARN dispatch: dispatch_decisions insert failed — {e}")

        # Generate route token
        route_token = _generate_route_token()
        try:
            sb.table("route_tokens").insert({
                "token": route_token,
                "client_id": client_id,
                "worker_id": worker_id,
                "session_id": session_id,
                "dispatch_date": dispatch_date,
                "expires_at": expires_at,
            }).execute()
        except Exception as e:
            print(f"[{timestamp()}] WARN dispatch: route_token insert failed — {e}")

        # Build the route notification body. Telnyx outbound is dead at the
        # carrier so we route through notify(), which falls back to email
        # (Resend) when SMS is blocked at any of the 3 permission layers.
        route_url = f"{base_url}/r/{route_token}"
        msg_type = "wave_assignment" if has_waves else "route"

        body_text = (
            f"{business_name} jobs for {dispatch_date}:\n"
            f"{len(jobs)} stop{'s' if len(jobs) != 1 else ''}.\n"
            f"Your route: {route_url}"
        )

        if has_waves:
            wave_lines = []
            for j in jobs:
                if j.get("wave_id"):
                    wave_lines.append(f"  Wave {j['wave_id']}: start {j.get('wave_start', 'TBD')}")
            if wave_lines:
                body_text += "\n" + "\n".join(wave_lines)

        # Notify the worker (notify() picks SMS or email based on the
        # 3-layer permission check; with the kill switch on it lands as email)
        if worker_phone:
            try:
                from execution.notify import notify
                subject = f"Today's route — {len(jobs)} stop{'s' if len(jobs) != 1 else ''}"
                result = notify(
                    client_id=client_id,
                    to_phone=worker_phone,
                    message=body_text,
                    subject=subject,
                    message_type=msg_type,
                )
                workers_notified += 1
                if result.get("success"):
                    sms_sent += 1
                    print(f"[{timestamp()}] INFO dispatch: route delivered to {worker_name} via {result.get('channel')}")
                else:
                    sms_failed.append({"worker": worker_name, "error": result.get("error", "blocked")})
                    print(f"[{timestamp()}] WARN dispatch: route delivery blocked for {worker_name} — {result.get('error')}")
            except Exception as e:
                sms_failed.append({"worker": worker_name, "error": str(e)})
                print(f"[{timestamp()}] ERROR dispatch: notify exception for {worker_name} — {e}")
        else:
            sms_failed.append({"worker": worker_name, "error": "No phone number"})
            print(f"[{timestamp()}] WARN dispatch: No phone for worker {worker_name}")

    print(
        f"[{timestamp()}] INFO dispatch: Send complete — "
        f"session={session_id[:8]} workers={workers_notified} sms={sms_sent} "
        f"failed={len(sms_failed)} jobs={total_jobs}"
    )

    return jsonify({
        "success": True,
        "session_id": session_id,
        "workers_notified": workers_notified,
        "sms_sent": sms_sent,
        "sms_failed": sms_failed,
        "total_jobs": total_jobs,
    })


# ---------------------------------------------------------------------------
# POST /api/jobs/reorder — persist drag-and-drop sort order
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/jobs/reorder", methods=["POST"])
def reorder_jobs():
    """
    Persist run order after drag-and-drop reorder on dispatch or control board.
    Body: { "moves": [ { "job_id": "uuid", "sort_order": 0, "assigned_worker_id": "uuid_or_null" }, ... ] }
    """
    client_id = _resolve_client_id()
    if not client_id:
        return jsonify({"success": False, "error": "not authenticated"}), 401

    data = request.get_json(silent=True) or {}
    moves = data.get("moves", [])

    if not moves:
        return jsonify({"success": True, "updated": 0})

    sb = _get_supabase()
    updated = 0

    for m in moves:
        job_id = m.get("job_id")
        sort_order = m.get("sort_order")
        worker_id = m.get("assigned_worker_id")

        if not job_id or sort_order is None:
            continue

        patch = {"sort_order": sort_order}
        if worker_id is not None:
            patch["assigned_worker_id"] = worker_id if worker_id != "" else None

        try:
            sb.table("jobs").update(patch).eq("id", job_id).eq("client_id", client_id).execute()
            updated += 1
        except Exception as e:
            print(f"[{timestamp()}] WARN reorder_jobs: job {job_id} — {e}")

    return jsonify({"success": True, "updated": updated})


# ---------------------------------------------------------------------------
# POST /api/dispatch/unassign — worker reschedules a job
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/dispatch/unassign", methods=["POST"])
def dispatch_unassign():
    """
    Unassign a job — called by RESCHEDULE button on worker route page.
    Token-validated (no session auth needed — workers don't have login).
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    token = data.get("token")

    if not job_id or not token:
        return jsonify({"success": False, "error": "job_id and token required"}), 400

    sb = _get_supabase()

    # Validate token
    try:
        tok = sb.table("route_tokens").select("client_id, expires_at").eq("token", token).execute()
        if not tok.data:
            return jsonify({"success": False, "error": "Invalid token"}), 403
        expires = tok.data[0].get("expires_at", "")
        if expires:
            expires_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires_dt:
                return jsonify({"success": False, "error": "Token expired"}), 403
        client_id = tok.data[0].get("client_id")
    except Exception as e:
        print(f"[{timestamp()}] ERROR dispatch: unassign token check failed — {e}")
        return jsonify({"success": False, "error": "Token validation failed"}), 500

    # Remove from route_assignments
    try:
        sb.table("route_assignments").delete().eq("job_id", job_id).eq("client_id", client_id).execute()
    except Exception as e:
        print(f"[{timestamp()}] WARN dispatch: unassign delete failed — {e}")

    # Set dispatch_status = unassigned
    try:
        sb.table("jobs").update({
            "dispatch_status": "unassigned",
            "assigned_worker_id": None,
        }).eq("id", job_id).execute()
    except Exception as e:
        print(f"[{timestamp()}] WARN dispatch: unassign job update failed — {e}")

    print(f"[{timestamp()}] INFO dispatch: Job {job_id[:8]} unassigned via worker route")
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# POST /api/dispatch/worker-action — direct tap handler (no SMS needed)
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/dispatch/worker-action", methods=["POST"])
def worker_action():
    """
    Direct API handler for worker status buttons on the route page.
    Works without SMS — tapping DONE/BACK/etc calls this endpoint directly.
    Token-validated (same as unassign).

    Replicates the same logic as _handle_worker_status_reply in sms_router.py
    so both paths (SMS and tap) produce the same result.
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    token = data.get("token")
    command = (data.get("command") or "").upper()

    if not job_id or not token or not command:
        return jsonify({"success": False, "error": "job_id, token, and command required"}), 400

    if command not in ("DONE", "BACK", "PARTS", "NOSHOW", "SCOPE"):
        return jsonify({"success": False, "error": f"Unknown command: {command}"}), 400

    sb = _get_supabase()

    # Validate token
    try:
        tok = sb.table("route_tokens").select("client_id, worker_id, expires_at").eq("token", token).execute()
        if not tok.data:
            return jsonify({"success": False, "error": "Invalid token"}), 403
        expires = tok.data[0].get("expires_at", "")
        if expires:
            expires_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires_dt:
                return jsonify({"success": False, "error": "Token expired"}), 403
        client_id = tok.data[0].get("client_id")
        worker_id = tok.data[0].get("worker_id")
    except Exception as e:
        print(f"[{timestamp()}] ERROR dispatch: worker-action token check failed — {e}")
        return jsonify({"success": False, "error": "Token validation failed"}), 500

    # Map command to status
    STATUS_MAP = {
        "DONE": "completed",
        "BACK": "carry_forward",
        "PARTS": "parts_pending",
        "NOSHOW": "no_show",
        "SCOPE": "scope_review",
    }
    new_status = STATUS_MAP[command]

    # Load job details
    job_name = "Job"
    customer_id = None
    customer_phone = None
    estimated_amount = 0
    job_type_val = "Service"
    try:
        job_row = sb.table("jobs").select(
            "customer_id, job_type, estimated_amount, job_description, customer_id"
        ).eq("id", job_id).execute()
        if job_row.data:
            jd = job_row.data[0]
            customer_id = jd.get("customer_id")
            estimated_amount = float(jd.get("estimated_amount") or 0)
            job_type_val = jd.get("job_type") or "Service"
            job_name = jd.get("job_description") or job_type_val
    except Exception:
        pass

    # Get customer name
    if customer_id:
        try:
            cust = sb.table("customers").select("customer_name, customer_phone").eq("id", customer_id).execute()
            if cust.data:
                job_name = cust.data[0].get("customer_name") or job_name
                customer_phone = cust.data[0].get("customer_phone")
        except Exception:
            pass

    # Get worker name
    worker_name = "Worker"
    try:
        w = sb.table("employees").select("name").eq("id", worker_id).execute()
        if w.data:
            worker_name = w.data[0].get("name", "Worker")
    except Exception:
        pass

    # Update job status
    try:
        from execution.db_jobs import update_job_status
        update_job_status(job_id, new_status)
    except Exception as e:
        print(f"[{timestamp()}] WARN dispatch: worker-action status update failed — {e}")

    result_msg = f"{job_name} marked {new_status.replace('_', ' ')}"

    # ── DONE: auto-create draft invoice ──────────────────────────
    if command == "DONE" and customer_id and estimated_amount > 0:
        try:
            invoice_desc = f"{job_type_val} — completed by {worker_name}"
            inv_result = sb.table("invoices").insert({
                "client_id": client_id,
                "customer_id": customer_id,
                "job_id": job_id,
                "invoice_text": invoice_desc,
                "amount_due": estimated_amount,
                "status": "draft",
            }).execute()
            if inv_result.data:
                inv_id = inv_result.data[0]["id"]
                print(f"[{timestamp()}] INFO dispatch: Auto-invoice created id={inv_id[:8]} amount=${estimated_amount:.2f}")
                result_msg += f" — invoice ${estimated_amount:.2f} created"
        except Exception as e:
            print(f"[{timestamp()}] WARN dispatch: Auto-invoice failed — {e}")

    # ── SCOPE: set scope_hold, notify owner ──────────────────────
    elif command == "SCOPE":
        try:
            import os as _os
            sb.table("jobs").update({
                "scope_hold": True,
                "job_notes": f"Scope change reported by {worker_name}. Pending owner review.",
            }).eq("id", job_id).execute()

            base_url = _os.environ.get("BOLTS11_BASE_URL", "https://web-production-043dc.up.railway.app")
            review_url = f"{base_url}/dashboard/job/{job_id}"

            # Notify owner
            try:
                client_row = sb.table("clients").select("owner_mobile, phone").eq("id", client_id).execute()
                if client_row.data:
                    owner_mobile = client_row.data[0].get("owner_mobile") or client_row.data[0].get("phone", "")
                    client_phone = client_row.data[0].get("phone", "")
                    if owner_mobile:
                        from execution.sms_send import send_sms
                        send_sms(
                            to_number=owner_mobile,
                            message_body=f"\u26a0\ufe0f Scope change — {job_name}\n{worker_name} flagged a change on site. Review:\n{review_url}",
                            from_number=client_phone,
                            message_type="scope_hold",
                        )
            except Exception:
                pass

            result_msg = f"{job_name} — scope hold, owner review required"
            print(f"[{timestamp()}] INFO dispatch: SCOPE hold set on job {job_id[:8]}")
        except Exception as e:
            print(f"[{timestamp()}] WARN dispatch: SCOPE hold failed — {e}")

    # ── NOSHOW: follow-up SMS to customer ────────────────────────
    elif command == "NOSHOW" and customer_phone:
        try:
            cust_consent = sb.table("customers").select("sms_consent").eq(
                "customer_phone", customer_phone
            ).eq("client_id", client_id).limit(1).execute()
            if cust_consent.data and cust_consent.data[0].get("sms_consent"):
                client_row = sb.table("clients").select("business_name, phone").eq("id", client_id).execute()
                biz_name = client_row.data[0].get("business_name", "your provider") if client_row.data else "your provider"
                client_phone = client_row.data[0].get("phone", "") if client_row.data else ""
                from execution.sms_send import send_sms
                send_sms(
                    to_number=customer_phone,
                    message_body=f"Hi, {biz_name} arrived for your appointment but no one was available. Please call to reschedule.",
                    from_number=client_phone,
                    message_type="no_show_followup",
                )
        except Exception as e:
            print(f"[{timestamp()}] WARN dispatch: NOSHOW follow-up failed — {e}")

    # Update dispatch_decisions (AI learning loop)
    try:
        sb.table("dispatch_decisions").update({
            "outcome_status": new_status,
            "outcome_at": datetime.now(timezone.utc).isoformat(),
        }).eq("job_id", job_id).execute()
    except Exception:
        pass

    # Log to agent_activity
    try:
        from execution.db_agent_activity import log_activity
        client_row = sb.table("clients").select("phone").eq("id", client_id).execute()
        client_phone = client_row.data[0].get("phone", "") if client_row.data else ""
        log_activity(
            client_phone=client_phone,
            agent_name="worker_route_tap",
            action_taken=f"{command.lower()}_job",
            input_summary=f"{worker_name}: {command} (tap)",
            output_summary=result_msg[:120],
            sms_sent=False,
        )
    except Exception:
        pass

    print(f"[{timestamp()}] INFO dispatch: Worker tap {command} on job {job_id[:8]} by {worker_name}")
    return jsonify({"success": True, "message": result_msg})


# ---------------------------------------------------------------------------
# GET /r/<token> — Worker route page (mobile, no login)
# ---------------------------------------------------------------------------

@dispatch_bp.route("/r/<token>")
def worker_route(token):
    """
    Public worker route page. No login required — token is the auth.
    Reads from jobs table (Option A) and employees table.
    """
    sb = _get_supabase()

    # Look up route token
    try:
        result = sb.table("route_tokens").select("*").eq("token", token).execute()
        if not result.data:
            print(f"[{timestamp()}] INFO dispatch: Route token not found — {token}")
            return render_template("error.html",
                title="Route Not Found",
                message="This route link is not valid.",
                sub="Check with your dispatcher for the correct link.",
            ), 404
        route = result.data[0]
    except Exception as e:
        print(f"[{timestamp()}] ERROR dispatch: Route token lookup failed — {e}")
        return render_template("error.html",
            title="Error",
            message="Something went wrong loading this route.",
            sub="Try again or contact your dispatcher.",
        ), 500

    # Check expiry — use token expires_at, not calendar date
    # Jobs stay active until completed/cancelled regardless of dispatch date
    dispatch_date_str = route.get("dispatch_date", "")
    try:
        expires_at_str = route.get("expires_at", "")
        if expires_at_str:
            expires_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires_dt:
                return render_template("error.html",
                    title="Route Expired",
                    message="This route link has expired.",
                    sub="Contact your dispatcher for a new link.",
                ), 410
    except (ValueError, TypeError):
        pass

    # Update viewed_at
    try:
        sb.table("route_tokens").update({
            "viewed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("token", token).execute()
    except Exception:
        pass

    # Load worker info from employees table
    worker_id = route.get("worker_id", "")
    worker_name = "Worker"
    try:
        w = sb.table("employees").select("name").eq("id", worker_id).execute()
        if w.data:
            worker_name = w.data[0].get("name", "Worker")
    except Exception:
        pass

    # Load client info
    client_id = route.get("client_id", "")
    business_name = "Bolts11"
    try:
        c = sb.table("clients").select("business_name").eq("id", client_id).execute()
        if c.data:
            business_name = c.data[0].get("business_name", "Bolts11")
    except Exception:
        pass

    # Load assigned jobs from route_assignments → jobs table
    session_id = route.get("session_id", "")
    jobs = []
    try:
        assignments = sb.table("route_assignments").select(
            "job_id, wave_id, sort_order"
        ).eq("session_id", session_id).eq("worker_id", worker_id).order("sort_order").execute()

        job_ids = [a["job_id"] for a in (assignments.data or [])]
        wave_map = {a["job_id"]: a.get("wave_id") for a in (assignments.data or [])}

        if job_ids:
            # Query jobs table (Option A) + customer data
            job_rows = sb.table("jobs").select(
                "id, job_type, job_description, job_notes, status, "
                "scheduled_date, customer_id, zone_cluster, requested_time, estimated_amount"
            ).in_("id", job_ids).execute()
            job_dict = {j["id"]: j for j in (job_rows.data or [])}

            # Batch-fetch customer names + addresses
            cust_ids = list(set(j.get("customer_id") for j in (job_rows.data or []) if j.get("customer_id")))
            cust_map = {}
            if cust_ids:
                try:
                    custs = sb.table("customers").select(
                        "id, customer_name, customer_phone, customer_address"
                    ).in_("id", cust_ids).execute().data or []
                    cust_map = {c["id"]: c for c in custs}
                except Exception:
                    pass

            # Rebuild in sort_order with customer data
            for jid in job_ids:
                if jid in job_dict:
                    j = job_dict[jid]
                    cust = cust_map.get(j.get("customer_id"), {})
                    j["customer_name"] = cust.get("customer_name", "")
                    j["customer_phone"] = cust.get("customer_phone", "")
                    j["address"] = cust.get("customer_address", "") or j.get("job_description", "")
                    j["notes"] = j.get("job_notes", "") or j.get("job_description", "")
                    j["wave_id"] = wave_map.get(jid)
                    jobs.append(j)
    except Exception as e:
        print(f"[{timestamp()}] ERROR dispatch: Failed to load route jobs — {e}")

    print(f"[{timestamp()}] INFO dispatch: Route {token} viewed by {worker_name} — {len(jobs)} jobs")

    # Get business phone for SMS buttons
    client_phone_for_sms = ""
    try:
        cp = sb.table("clients").select("phone").eq("id", client_id).execute()
        if cp.data:
            client_phone_for_sms = cp.data[0].get("phone", "")
    except Exception:
        pass

    return render_template("worker_route.html",
        business_name=business_name,
        worker_name=worker_name,
        dispatch_date=dispatch_date_str,
        jobs=jobs,
        token=token,
        business_phone=client_phone_for_sms,
    )

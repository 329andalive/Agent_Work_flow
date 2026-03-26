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
    client_phone = client.get("phone", "")
    business_name = client.get("business_name", "Bolts11")
    base_url = os.environ.get("BOLTS11_BASE_URL", "https://bolts11.com")
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()

    sb = _get_supabase()
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

        # Build SMS body
        route_url = f"{base_url}/r/{route_token}"
        msg_type = "wave_assignment" if has_waves else "route"

        sms_body = (
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
                sms_body += "\n" + "\n".join(wave_lines)

        # Send SMS
        if worker_phone:
            try:
                from execution.sms_send import send_sms
                result = send_sms(
                    to_number=worker_phone,
                    message_body=sms_body,
                    from_number=client_phone,
                    message_type=msg_type,
                )
                if result.get("success"):
                    sms_sent += 1
                    workers_notified += 1
                    print(f"[{timestamp()}] INFO dispatch: SMS sent to {worker_name} ({worker_phone})")
                else:
                    sms_failed.append({"worker": worker_name, "error": result.get("error", "unknown")})
                    workers_notified += 1
                    print(f"[{timestamp()}] WARN dispatch: SMS failed for {worker_name} — {result.get('error')}")
            except Exception as e:
                sms_failed.append({"worker": worker_name, "error": str(e)})
                print(f"[{timestamp()}] ERROR dispatch: SMS exception for {worker_name} — {e}")
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

    # Check expiry — past midnight of dispatch_date
    dispatch_date_str = route.get("dispatch_date", "")
    try:
        from datetime import date as date_cls
        dispatch_dt = date_cls.fromisoformat(dispatch_date_str)
        if date_cls.today() > dispatch_dt:
            return render_template("error.html",
                title="Route Expired",
                message=f"This route was for {dispatch_date_str}.",
                sub="Contact your dispatcher for today's route.",
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

    return render_template("worker_route.html",
        business_name=business_name,
        worker_name=worker_name,
        dispatch_date=dispatch_date_str,
        jobs=jobs,
        token=token,
    )

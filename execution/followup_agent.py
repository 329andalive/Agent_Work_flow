"""
followup_agent.py — Proactive follow-up, proposal response handling, and loss tracking

Three entry points called from different places:

  1. run_scheduled_followups()
        Called by cron_runner every 30 minutes.
        Queries follow_ups table for pending records due now and sends them.
        Handles: estimate_followup, payment_chase, seasonal_reminder

  2. handle_proposal_response(client_phone, customer_phone, raw_input)
        Called by sms_router when a customer texts back about a proposal.
        Handles: accepted, declined

  3. handle_loss_reason(client_phone, customer_phone, raw_input)
        Called by sms_router when owner texts the "why did you lose it" answer.
        Handles: loss_reason (numeric 1-4 or written keywords)

Usage:
    from execution.followup_agent import (
        run_scheduled_followups,
        handle_proposal_response,
        handle_loss_reason,
    )
"""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.db_connection import get_client as get_supabase
from execution.db_client import get_client_by_phone, get_personality
from execution.db_customer import get_customer_by_phone
from execution.db_proposals import (
    update_proposal_response,
    get_latest_sent_proposal_for_customer,
    get_cold_proposals,
)
from execution.db_followups import (
    get_due_followups,
    mark_followup_sent,
    cancel_followups_for_job,
    get_pending_followups_by_type,
    schedule_followup,
)
from execution.db_lost_jobs import (
    save_lost_job,
    update_lost_job_reason,
    update_monthly_outcomes,
)
from execution.db_jobs import update_job_status, get_job
from execution.call_claude import call_claude
from execution.sms_send import send_sms
from execution.response_detector import extract_loss_reason


def _timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Claude prompt helpers
# ---------------------------------------------------------------------------

def _generate_followup_message(
    followup_type: str,
    personality: str,
    job_description: str,
    customer_name: str,
    touch_number: int,
) -> str | None:
    """
    Ask Claude to write a follow-up SMS in the owner's voice.
    Returns the message text or None on failure.
    """
    directive = f"""
You write follow-up text messages for a trades business owner.
Write EXACTLY ONE short SMS (under 160 characters) in the owner's voice.
No quotes around the message. No labels. Just the message text itself.
Sound like a real person texting — casual, brief, professional.
Never say 'just checking in'. Never use exclamation points.
This is touch #{touch_number} for this job.

Owner personality and rates:
{personality}
"""

    if followup_type == "estimate_followup":
        user_prompt = (
            f"Write a follow-up text to {customer_name} checking on the estimate "
            f"for: {job_description}. Be brief. Ask if they have questions or are ready to schedule."
        )
    elif followup_type == "payment_chase":
        user_prompt = (
            f"Write a friendly payment reminder to {customer_name} about the invoice "
            f"for: {job_description}. Keep it short and professional."
        )
    elif followup_type == "seasonal_reminder":
        user_prompt = (
            f"Write a seasonal check-in text to {customer_name}. "
            f"Reference the past work: {job_description}. "
            f"Mention it might be a good time to schedule maintenance."
        )
    else:
        user_prompt = (
            f"Write a brief follow-up text to {customer_name} about: {job_description}."
        )

    return call_claude(
        system_prompt=directive.strip(),
        user_prompt=user_prompt,
        model="haiku",
        max_tokens=200,
    )


def _generate_cold_message(
    personality: str,
    job_description: str,
    customer_name: str,
) -> str | None:
    """
    Generate the final 'going cold' message that asks why they lost the job.
    """
    directive = f"""
You write text messages for a trades business owner.
Write EXACTLY ONE short SMS (under 200 characters) in the owner's voice.
This is the final message — the job has gone cold (no response in 14 days).
Acknowledge you understand if they went another direction.
Ask if they'd be willing to share why (price, timing, another contractor, etc.).
No quotes, no labels, just the message text.

Owner personality:
{personality}
"""
    user_prompt = (
        f"Write a final follow-up to {customer_name} about the estimate for: {job_description}. "
        f"Acknowledge it's been a while, wish them well, and ask for brief feedback."
    )

    return call_claude(
        system_prompt=directive.strip(),
        user_prompt=user_prompt,
        model="haiku",
        max_tokens=250,
    )


def _generate_acceptance_reply(
    personality: str,
    job_description: str,
    customer_name: str,
) -> str | None:
    """
    Generate a confirmation reply when a customer accepts.
    """
    directive = f"""
You write text messages for a trades business owner.
Write EXACTLY ONE short SMS (under 160 characters) confirming you got the yes.
Sound excited but not over the top. Tell them you'll be in touch to schedule.
No quotes, no labels, just the message text.

Owner personality:
{personality}
"""
    user_prompt = (
        f"Write a reply to {customer_name} who just accepted the estimate "
        f"for: {job_description}."
    )

    return call_claude(
        system_prompt=directive.strip(),
        user_prompt=user_prompt,
        model="haiku",
        max_tokens=160,
    )


# ---------------------------------------------------------------------------
# Entry point 1: Run scheduled follow-ups (called by cron)
# ---------------------------------------------------------------------------

def run_scheduled_followups() -> int:
    """
    Find all pending follow-ups due now and send them.
    Also checks for cold proposals (14+ days, no response) and handles them.

    Returns:
        Number of follow-ups processed.
    """
    count = 0
    supabase = get_supabase()

    # --- Send due follow-ups ---
    due = get_due_followups()
    print(f"[{_timestamp()}] INFO followup_agent: {len(due)} follow-ups due")

    for fu in due:
        client_id   = fu.get("client_id")
        customer_id = fu.get("customer_id")
        job_id      = fu.get("job_id")
        followup_id = fu.get("id")
        ftype       = fu.get("follow_up_type")

        # Look up client phone and personality
        try:
            client_row = supabase.table("clients").select("phone, personality").eq("id", client_id).single().execute()
            client_phone = client_row.data["phone"]
            personality  = client_row.data.get("personality", "")
        except Exception as e:
            print(f"[{_timestamp()}] ERROR followup_agent: client lookup failed for {client_id} — {e}")
            continue

        # Look up customer
        try:
            customer_row = supabase.table("customers").select("phone, name").eq("id", customer_id).single().execute()
            customer_phone = customer_row.data["phone"]
            customer_name  = customer_row.data.get("name", "there")
        except Exception as e:
            print(f"[{_timestamp()}] ERROR followup_agent: customer lookup for {customer_id} — {e}")
            continue

        # Look up job description
        job = get_job(job_id) if job_id else None
        job_description = (job.get("job_description") or job.get("raw_input") or "your recent job") if job else "your recent job"

        # Count how many touches already sent for this proposal
        proposal_id = fu.get("proposal_id")
        touch_number = 1
        if proposal_id:
            from execution.db_followups import count_followups_sent_for_proposal
            touch_number = count_followups_sent_for_proposal(proposal_id) + 1

        # Generate and send the message
        msg = _generate_followup_message(
            followup_type=ftype,
            personality=personality,
            job_description=job_description,
            customer_name=customer_name,
            touch_number=touch_number,
        )

        if not msg:
            print(f"[{_timestamp()}] ERROR followup_agent: Claude returned None for followup {followup_id}")
            continue

        result = send_sms(to_number=customer_phone, message_body=msg, from_number=client_phone)
        if result.get("success"):
            mark_followup_sent(followup_id, msg)
            print(f"[{_timestamp()}] INFO followup_agent: Sent {ftype} to {customer_phone}")
            count += 1
        else:
            print(f"[{_timestamp()}] ERROR followup_agent: SMS failed for followup {followup_id} — {result.get('error')}")

    # --- Check for cold proposals ---
    _process_cold_proposals()

    return count


def _process_cold_proposals():
    """
    Find proposals 14+ days old with no response. Mark them cold, send final message,
    ask owner why they lost it, record in lost_jobs.
    """
    supabase = get_supabase()

    # Get all clients
    try:
        clients_result = supabase.table("clients").select("id, phone, personality").execute()
        clients = clients_result.data or []
    except Exception as e:
        print(f"[{_timestamp()}] ERROR followup_agent: cold proposal client fetch — {e}")
        return

    for client in clients:
        client_id    = client["id"]
        client_phone = client["phone"]
        personality  = client.get("personality", "")

        cold_proposals = get_cold_proposals(client_id, days=14)

        for proposal in cold_proposals:
            proposal_id  = proposal["id"]
            customer_id  = proposal["customer_id"]
            job_id       = proposal["job_id"]
            amount       = float(proposal.get("amount_estimate") or 0)

            # Mark proposal response_type=cold
            update_proposal_response(proposal_id, "cold")

            # Cancel remaining follow-ups for this job
            if job_id:
                cancel_followups_for_job(job_id)
                update_job_status(job_id, "lost")

            # Look up customer
            try:
                customer_row = supabase.table("customers").select("phone, name").eq("id", customer_id).single().execute()
                customer_phone = customer_row.data["phone"]
                customer_name  = customer_row.data.get("name", "there")
            except Exception as e:
                print(f"[{_timestamp()}] ERROR followup_agent: cold customer lookup — {e}")
                continue

            # Look up job
            job = get_job(job_id) if job_id else None
            job_description = (job.get("job_description") or job.get("raw_input") or "the recent job") if job else "the recent job"

            # Send final cold message to customer
            cold_msg = _generate_cold_message(personality, job_description, customer_name)
            if cold_msg:
                send_sms(to_number=customer_phone, message_body=cold_msg, from_number=client_phone)

            # Record in lost_jobs
            save_lost_job(
                client_id=client_id,
                customer_id=customer_id,
                job_id=job_id,
                proposal_id=proposal_id,
                proposal_amount=amount,
                lost_reason="unknown",
            )

            # Ask owner why they lost it
            why_msg = (
                "Hey, looks like the job at {}'s place went cold. "
                "Do you know why? Reply: 1=price, 2=timing, 3=competitor, 4=relationship"
            ).format(customer_name)
            send_sms(to_number=client_phone, message_body=why_msg, from_number=client_phone)

            # Schedule a pending follow-up record so sms_router knows to watch for the reply
            schedule_followup(
                client_id=client_id,
                customer_id=customer_id,
                job_id=job_id,
                proposal_id=proposal_id,
                followup_type="lost_job_why",
                scheduled_for=datetime.now(timezone.utc).isoformat(),
            )

            # Update monthly outcomes
            update_monthly_outcomes(client_id)

            print(f"[{_timestamp()}] INFO followup_agent: Marked cold proposal={proposal_id}, notified owner")


# ---------------------------------------------------------------------------
# Entry point 2: Handle proposal response from customer
# ---------------------------------------------------------------------------

def handle_proposal_response(
    client_phone: str,
    customer_phone: str,
    raw_input: str,
    response_type: str,
) -> None:
    """
    Handle a customer's accept or decline reply to a proposal.

    Args:
        client_phone:  Telnyx number (used to look up client)
        customer_phone: Customer's phone number
        raw_input:     Raw SMS text from customer
        response_type: "accepted" or "declined"
    """
    # Look up client
    client = get_client_by_phone(client_phone)
    if not client:
        print(f"[{_timestamp()}] ERROR followup_agent: client not found for {client_phone}")
        return

    client_id   = client["id"]
    personality = client.get("personality", "")

    # Look up customer
    customer = get_customer_by_phone(client_id, customer_phone)
    if not customer:
        print(f"[{_timestamp()}] ERROR followup_agent: customer not found {customer_phone}")
        return

    customer_id   = customer["id"]
    customer_name = customer.get("name", "there")

    # Find latest sent proposal for this customer
    proposal = get_latest_sent_proposal_for_customer(client_id, customer_id)
    if not proposal:
        print(f"[{_timestamp()}] ERROR followup_agent: no open proposal for customer {customer_id}")
        return

    proposal_id = proposal["id"]
    job_id      = proposal.get("job_id")
    amount      = float(proposal.get("amount_estimate") or 0)

    # Cancel pending follow-ups — job is resolved
    if job_id:
        cancel_followups_for_job(job_id)

    if response_type == "accepted":
        # Update proposal
        update_proposal_response(proposal_id, "accepted")
        if job_id:
            update_job_status(job_id, "scheduled")

        # Generate and send confirmation to customer
        job = get_job(job_id) if job_id else None
        job_description = (job.get("job_description") or job.get("raw_input") or "the job") if job else "the job"

        reply = _generate_acceptance_reply(personality, job_description, customer_name)
        if reply:
            send_sms(to_number=customer_phone, message_body=reply, from_number=client_phone)

        # Notify owner
        owner_msg = f"Job accepted by {customer_name}! ${amount:,.0f} proposal confirmed. Time to schedule."
        send_sms(to_number=client_phone, message_body=owner_msg, from_number=client_phone)

        print(f"[{_timestamp()}] INFO followup_agent: Proposal {proposal_id} ACCEPTED by {customer_name}")

    elif response_type == "declined":
        # Update proposal
        update_proposal_response(proposal_id, "declined")
        if job_id:
            update_job_status(job_id, "lost")

        # Record lost job
        save_lost_job(
            client_id=client_id,
            customer_id=customer_id,
            job_id=job_id,
            proposal_id=proposal_id,
            proposal_amount=amount,
            lost_reason="unknown",
        )

        # Ask owner why
        why_msg = (
            f"{customer_name} declined the ${amount:,.0f} quote. "
            f"Do you know why? Reply: 1=price, 2=timing, 3=competitor, 4=relationship"
        )
        send_sms(to_number=client_phone, message_body=why_msg, from_number=client_phone)

        # Schedule lost_job_why follow-up so router knows to watch for reply
        schedule_followup(
            client_id=client_id,
            customer_id=customer_id,
            job_id=job_id,
            proposal_id=proposal_id,
            followup_type="lost_job_why",
            scheduled_for=datetime.now(timezone.utc).isoformat(),
        )

        # Update monthly outcomes
        update_monthly_outcomes(client_id)

        print(f"[{_timestamp()}] INFO followup_agent: Proposal {proposal_id} DECLINED by {customer_name}")


# ---------------------------------------------------------------------------
# Entry point 3: Handle owner's loss reason reply
# ---------------------------------------------------------------------------

def handle_loss_reason(
    client_phone: str,
    customer_phone: str,
    raw_input: str,
) -> None:
    """
    Owner has replied to the "why did you lose it" question.
    Extract the reason, update the lost_job record, update monthly outcomes.

    Args:
        client_phone:   The owner's Telnyx number (= to_number in inbound SMS)
        customer_phone: The owner's real phone (= from_number in inbound SMS)
        raw_input:      Raw SMS text from the owner
    """
    client = get_client_by_phone(client_phone)
    if not client:
        print(f"[{_timestamp()}] ERROR followup_agent: client not found for {client_phone}")
        return

    client_id = client["id"]

    # Extract reason code from the reply
    reason_code, detail_text = extract_loss_reason(raw_input)

    # Find the most recent pending lost_job_why follow-up for this client
    pending = get_pending_followups_by_type(client_id, "lost_job_why")
    if not pending:
        print(f"[{_timestamp()}] WARN followup_agent: no pending lost_job_why for client {client_id}")
        return

    # Use the most recent one
    fu = pending[0]
    proposal_id = fu.get("proposal_id")
    followup_id = fu.get("id")

    # Update the lost_job record
    if proposal_id:
        update_lost_job_reason(
            proposal_id=proposal_id,
            lost_reason=reason_code,
            lost_reason_detail=detail_text,
        )
        # Also stamp lost_reason on the proposal row directly
        try:
            get_supabase().table("proposals").update({
                "lost_reason":        reason_code,
                "lost_reason_detail": detail_text,
            }).eq("id", proposal_id).execute()
        except Exception as e:
            print(f"[{_timestamp()}] ERROR followup_agent: proposal lost_reason update — {e}")

    # Mark the follow-up as sent/resolved
    mark_followup_sent(followup_id, f"owner replied: {raw_input}")

    # Update monthly outcomes now that reason is recorded
    update_monthly_outcomes(client_id)

    # Confirm receipt to owner
    reason_labels = {
        "price": "price",
        "timing": "timing/availability",
        "competition": "went with another contractor",
        "relationship": "personal connection",
        "unknown": "unknown",
    }
    label = reason_labels.get(reason_code, reason_code)
    confirm_msg = f"Got it — logged as: {label}. Thanks for tracking that."
    send_sms(to_number=customer_phone, message_body=confirm_msg, from_number=client_phone)

    print(f"[{_timestamp()}] INFO followup_agent: Loss reason recorded: {reason_code} for proposal {proposal_id}")

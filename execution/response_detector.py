"""
response_detector.py — Pure keyword detection for inbound SMS responses

No database calls. No side effects. Takes raw text, returns intent label.
Used by sms_router.py to classify every inbound message before routing.

Priority order callers must respect:
  1. loss_reason     — owner answering a "why did you lose it" question
  2. accepted        — customer confirming a proposal
  3. declined        — customer declining a proposal
  4. lost_report     — owner proactively reporting a loss
  5. (then fall through to invoice / proposal / default routing)

Usage:
    from execution.response_detector import detect_response_type, extract_loss_reason
    intent = detect_response_type("yeah sounds good, book it")
    # → "accepted"
"""

import re
from datetime import datetime


# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

ACCEPTED_KEYWORDS = [
    "yes", "yeah", "yep", "yup", "absolutely",
    "lets do it", "let's do it", "sounds good", "book it",
    "schedule it", "go ahead", "confirmed", "confirm",
    "approved", "do it", "when can you", "what time",
    "can you come", "accepted", "great", "perfect",
    "works for me", "sign me up", "lets go", "let's go",
    "get me on the schedule",
]

DECLINED_KEYWORDS = [
    "no thanks", "no thank you", "not right now", "too expensive",
    "too much", "going another way", "went with someone",
    "found someone", "got someone", "nevermind", "never mind",
    "not interested", "cancel", "pass", "passed",
    "don't need it", "dont need it", "not gonna", "not going to",
    "already taken care", "got it handled", "not for me",
    "going with someone else",
]

# Owner telling the system they lost a job — distinct from customer declining
LOST_REPORT_KEYWORDS = [
    "lost it", "lost the job", "they went with", "went with someone",
    "went with another", "customer went with", "didnt get it",
    "didn't get it", "lost the", "lost anderson", "lost mike",
    "lost the bid", "lost that one", "we lost", "lost the quote",
    "no go on", "fell through",
]

# Owner answering the "why did you lose it" question
# Matches numbered replies (1/2/3/4) OR written reasons
LOSS_REASON_KEYWORDS = {
    "price": [
        "price", "too expensive", "cheaper", "beat my price",
        "undercut", "lower price", "cost", "money",
        "couldn't match", "too high",
    ],
    "timing": [
        "timing", "too busy", "couldn't wait", "wait too long",
        "not available", "too far out", "schedule",
        "couldn't get there", "took too long",
    ],
    "competition": [
        "competition", "competitor", "another company", "another contractor",
        "other guy", "different company", "went local",
        "someone else", "another septic",
    ],
    "relationship": [
        "knew someone", "relationship", "neighbor", "family",
        "brother in law", "friend", "cousin", "knew a guy",
        "personal connection", "buddy",
    ],
    "unknown": [
        "not sure", "unknown", "don't know", "dont know",
        "no idea", "couldn't tell", "not certain",
    ],
}

# Numeric shortcodes for loss reason (1=price, 2=timing, 3=competition, 4=relationship)
LOSS_REASON_CODES = {
    "1": "price",
    "2": "timing",
    "3": "competition",
    "4": "relationship",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_response_type(raw_input: str) -> str:
    """
    Classify an inbound SMS into one of five intent categories.

    Returns one of:
        "accepted"    — customer confirmed a proposal
        "declined"    — customer declined a proposal
        "lost_report" — owner proactively reporting a loss
        "loss_reason" — owner answering the why-did-you-lose question
        "unknown"     — no match, let caller use fallback routing

    Callers must check in priority order:
        loss_reason → accepted → declined → lost_report → unknown
    """
    text = raw_input.strip().lower()

    # Check numeric shortcode first (1, 2, 3, 4) — unambiguous
    if re.fullmatch(r'\s*[1-4]\s*', text):
        return "loss_reason"

    # Check loss reason written keywords
    for _reason, keywords in LOSS_REASON_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                # Only call this loss_reason if it's a short reply
                # (longer messages with these words might be something else)
                if len(text.split()) <= 8:
                    return "loss_reason"

    # Check acceptance
    for kw in ACCEPTED_KEYWORDS:
        if kw in text:
            return "accepted"

    # Check decline
    for kw in DECLINED_KEYWORDS:
        if kw in text:
            return "declined"

    # Check owner loss report
    for kw in LOST_REPORT_KEYWORDS:
        if kw in text:
            return "lost_report"

    return "unknown"


def extract_loss_reason(raw_input: str) -> tuple[str, str]:
    """
    Map the owner's reply to a standard reason code and extract detail text.

    Args:
        raw_input: Owner's reply (e.g. "1", "price", "they knew someone")

    Returns:
        (reason_code, detail_text)
        reason_code: "price", "timing", "competition", "relationship", "unknown"
        detail_text: original raw_input preserved for context
    """
    text = raw_input.strip().lower()

    # Numeric shortcode → direct mapping
    stripped = text.strip()
    if stripped in LOSS_REASON_CODES:
        return LOSS_REASON_CODES[stripped], raw_input.strip()

    # Keyword scan
    for reason_code, keywords in LOSS_REASON_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return reason_code, raw_input.strip()

    return "unknown", raw_input.strip()

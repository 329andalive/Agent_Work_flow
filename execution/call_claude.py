"""
call_claude.py — Shared Claude API wrapper for all agents

Every agent in the stack calls Claude through this module.
One place to manage model versions, retry logic, and cost logging.

Usage:
    from execution.call_claude import call_claude

    response = call_claude(
        system_prompt="You are ...",
        user_prompt="Generate a proposal for ...",
        model="sonnet"   # "haiku", "sonnet", or "opus"
    )
"""

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import anthropic

load_dotenv()

# ---------------------------------------------------------------------------
# Model name → exact Anthropic model ID
# Update these strings when new model versions are released.
# ---------------------------------------------------------------------------
MODEL_MAP = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-6",
}

# Max tokens for the response — 1600 chars of SMS needs ~400 tokens
# Set higher so Claude has room to think before trimming
DEFAULT_MAX_TOKENS = 1024

# SDK auto-retries 429 and 5xx — we set max_retries=2 explicitly
MAX_RETRIES = 2


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def call_claude(
    system_prompt: str,
    user_prompt: str,
    model: str = "sonnet",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str | None:
    """
    Make a single Claude API call and return the response text.

    Args:
        system_prompt: Instructions for how Claude should behave
        user_prompt:   The actual task or content to process
        model:         One of "haiku", "sonnet", or "opus"
        max_tokens:    Maximum tokens in the response (default 1024)

    Returns:
        Response text as a string, or None if the call fails after retries.
    """
    # Resolve model name to exact ID
    model_id = MODEL_MAP.get(model.lower())
    if not model_id:
        print(f"[{timestamp()}] ERROR call_claude: Unknown model '{model}'. Use haiku, sonnet, or opus.")
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"[{timestamp()}] ERROR call_claude: ANTHROPIC_API_KEY not set in .env")
        return None

    # Initialize the client with the SDK's built-in retry logic
    client = anthropic.Anthropic(
        api_key=api_key,
        max_retries=MAX_RETRIES,
    )

    print(f"[{timestamp()}] INFO call_claude: Calling {model_id} | max_tokens={max_tokens}")

    try:
        response = client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt}
            ],
        )

        # Log token usage for cost tracking
        usage = response.usage
        print(
            f"[{timestamp()}] INFO call_claude: Done. "
            f"input_tokens={usage.input_tokens} "
            f"output_tokens={usage.output_tokens} "
            f"stop_reason={response.stop_reason}"
        )

        # Pull the text content out of the response
        text = next(
            (block.text for block in response.content if block.type == "text"),
            None
        )

        if text is None:
            print(f"[{timestamp()}] WARN call_claude: Response contained no text block")

        return text

    except anthropic.AuthenticationError:
        print(f"[{timestamp()}] ERROR call_claude: Invalid ANTHROPIC_API_KEY")
        return None

    except anthropic.RateLimitError as e:
        # SDK already retried MAX_RETRIES times — log and give up
        retry_after = e.response.headers.get("retry-after", "unknown")
        print(f"[{timestamp()}] ERROR call_claude: Rate limited after {MAX_RETRIES} retries. retry-after={retry_after}s")
        return None

    except anthropic.BadRequestError as e:
        print(f"[{timestamp()}] ERROR call_claude: Bad request — {e.message}")
        return None

    except anthropic.APIStatusError as e:
        print(f"[{timestamp()}] ERROR call_claude: API error {e.status_code} — {e.message}")
        return None

    except anthropic.APIConnectionError:
        print(f"[{timestamp()}] ERROR call_claude: Connection error — check network")
        return None

    except Exception as e:
        print(f"[{timestamp()}] ERROR call_claude: Unexpected error — {e}")
        return None

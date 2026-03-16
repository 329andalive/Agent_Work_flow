"""
test_sms.py — End-to-end test: send a real SMS via Telnyx

This script verifies that:
  1. Credentials in .env are valid
  2. The Telnyx API accepts our request
  3. sms_send.py works correctly

Usage:
    python execution/test_sms.py

Edit TEST_RECIPIENT below to your own number before running.
"""

import sys
import os

# Allow running from the project root without installing as a package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.sms_send import send_sms
from datetime import datetime

# ---------------------------------------------------------------------------
# EDIT THIS — the number that should receive the test SMS
# Must be in E.164 format: +1XXXXXXXXXX
# ---------------------------------------------------------------------------
TEST_RECIPIENT = "+1XXXXXXXXXX"   # ← replace with your number


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    print(f"[{timestamp()}] Starting end-to-end SMS test...")
    print(f"[{timestamp()}] Sending test message to: {TEST_RECIPIENT}")

    # Send a short test message
    result = send_sms(
        to_number=TEST_RECIPIENT,
        message_body="[Trades AI] Test message — SMS pipeline is working. ✓",
    )

    print()
    if result["success"]:
        print("=" * 50)
        print("  ✓ SUCCESS")
        print(f"  message_id: {result['message_id']}")
        print("=" * 50)
        print()
        print("Next steps:")
        print("  1. Check your phone — you should receive the SMS within ~10 seconds")
        print("  2. Run the Flask server:  python execution/sms_receive.py")
        print("  3. Expose it with ngrok:  ngrok http 5000")
    else:
        print("=" * 50)
        print("  ✗ FAILED")
        print(f"  error: {result['error']}")
        print("=" * 50)
        print()
        print("Troubleshooting:")
        print("  - Check TELNYX_API_KEY is set correctly in .env")
        print("  - Check TELNYX_PHONE_NUMBER is set in .env")
        print("  - Verify the API key has messaging permissions in Telnyx portal")
        sys.exit(1)


if __name__ == "__main__":
    main()

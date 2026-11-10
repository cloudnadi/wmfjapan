"""
Check-in URL for a seat purchase's ticket.

Deliberately independent of any host-owned entry-pass/QR mechanism (e.g.
this repo's own lambdas/qr_email.py, built around registrationPassToken) —
a seat purchase's ticket carries its own `checkInToken` (see
seat_purchase_logic.update_purchase_status) and its own check-in URL, so it
can never be confused with, or accidentally admit against, an unrelated
check-in flow the host already has for a different kind of admission.

Env vars: FRONTEND_URL — the check-in QR encodes
  {FRONTEND_URL}/seat-check-in?token={checkInToken}
Omit it (or leave the purchase's checkInToken unset) and check_in_url simply
returns "", which seat_purchase_emailer.py takes as "no ticket to attach".
"""

import os

FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")


def check_in_url(token: str) -> str:
    return f"{FRONTEND_URL}/seat-check-in?token={token}" if FRONTEND_URL and token else ""

#!/usr/bin/env python3
"""Non-destructive smoke test of a deployed event platform (preview or production).

  python3 scripts/smoke_preview.py --site https://wmfjapan-preview.cloudnadi.com \\
      --api https://<id>.execute-api.ap-northeast-1.amazonaws.com/<stage> [--admin-token <Cognito ID token>]

Reads only, plus requests that are designed to be rejected (nothing is created). With --admin-token the admin
read routes are exercised too. Exit code 1 if any check fails; a 5xx anywhere is always a failure.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

results = []


def call(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            raw, status, hdrs = res.read(), res.status, dict(res.headers)
    except urllib.error.HTTPError as exc:
        raw, status, hdrs = exc.read(), exc.code, dict(exc.headers)
    except (urllib.error.URLError, OSError) as exc:
        raw, status, hdrs = str(exc).encode(), 0, {}
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        parsed = {"_raw": raw[:200].decode("utf-8", "replace")}
    return status, parsed, hdrs


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS" if ok else "FAIL"), name, ("- " + str(detail)[:220]) if (detail and not ok) else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--api", required=True)
    ap.add_argument("--admin-token", default="")
    a = ap.parse_args()
    site, api = a.site.rstrip("/"), a.api.rstrip("/")

    status, body, _ = call("GET", site + "/")
    check("site serves the app", status == 200, status)

    status, body, hdrs = call("GET", f"{api}/public/events")
    check("public event list", status == 200 and isinstance(body.get("events"), list), (status, body))
    events = body.get("events", []) if status == 200 else []
    check("listing hides internal fields", all("formId" not in e and "createdBy" not in e for e in events))

    status, body, _ = call("GET", f"{api}/public/events/definitely-not-an-event")
    check("unknown event is 404", status == 404, (status, body))

    status, body, hdrs = call("OPTIONS", f"{api}/public/events")
    check("CORS preflight", status == 200 and any(k.lower() == "access-control-allow-origin" for k in hdrs), (status, hdrs))

    if events:
        slug = events[0]["slug"]
        status, page, _ = call("GET", f"{api}/public/events/{slug}")
        ev = page.get("event") or {}
        check(f"event page /{slug}", status == 200 and "tickets" in ev, (status, page))
        tickets = ev.get("tickets") or []
        fields = {f.get("key"): f for f in (ev.get("form") or {}).get("fields", [])}
        if fields:
            check("form: WhatsApp number is required", bool(fields.get("phone", {}).get("required")), fields.get("phone"))
        status, body, _ = call("POST", f"{api}/public/events/{slug}/quote", {"tickets": {"no-such-ticket": 1}})
        check("quote rejects an unknown ticket (400, not 5xx)", status == 400, (status, body))
        sign_in_only = (ev.get("registration") or {}).get("reason") == "login_required"
        status, body, _ = call("POST", f"{api}/public/events/{slug}/register", {})
        if sign_in_only:
            check("sign-in-only event refuses an anonymous registration (403)", status == 403 and body.get("code") == "login_required", (status, body))
        else:
            check("empty registration is rejected cleanly", status in (400, 401, 409), (status, body))
            if tickets:
                status, body, _ = call("POST", f"{api}/public/events/{slug}/register", {
                    "tickets": {tickets[0]["ticketTypeId"]: 1}, "idempotencyKey": "smoke-invalid",
                    "answers": {"fullName": "Smoke", "email": "not-an-email", "phone": "abc", "consentPrivacy": True, "consentTerms": True}})
                check("invalid answers give field errors (400)", status == 400 and body.get("fields"), (status, body))
    else:
        print("SKIP event page checks (no published events on this environment)")

    status, body, _ = call("POST", f"{api}/webhooks/event-registrations/square", {"type": "payment.updated"},
                           {"x-square-hmacsha256-signature": "bad"})
    check("webhook refuses a bad signature (401)", status == 401, (status, body))

    status, body, _ = call("GET", f"{api}/admin/event-platform/me")
    check("admin API requires login", status in (401, 403), (status, body))

    if a.admin_token:
        auth = {"Authorization": a.admin_token}
        status, body, _ = call("GET", f"{api}/admin/event-platform/me", headers=auth)
        check("admin /me", status == 200 and body.get("roles"), (status, body))
        status, body, _ = call("GET", f"{api}/admin/event-platform/events", headers=auth)
        check("admin event list", status == 200, (status, body))
        for ev in (body.get("events") or [])[:3]:
            for sub in ("dashboard", "registrations"):
                status, b, _ = call("GET", f"{api}/admin/event-platform/events/{ev['eventId']}/{sub}", headers=auth)
                check(f"admin {sub} for {ev['slug']}", status in (200, 409), (status, b))   # 409 = legacy mirror, no tools

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

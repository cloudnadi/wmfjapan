"""In-memory stand-ins for the event-platform store and payment adapter (same semantics, same exceptions)."""

import copy
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))

import event_model  # noqa: E402
import event_registrations as logic  # noqa: E402

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=JST)


class FakeStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.events, self.tickets, self.forms, self.regs = {}, {}, {}, {}
        self.counters, self.members, self.txns, self.config, self.rl = {}, {}, {}, {}, {}
        self.calls = []

    # catalogue
    def get_event(self, eid): return copy.deepcopy(self.events.get(eid))
    def get_event_by_slug(self, slug): return next((copy.deepcopy(e) for e in self.events.values() if e["slug"] == slug), None)
    def list_listed_events(self): return [copy.deepcopy(e) for e in self.events.values() if e["status"] in ("published", "closed")]
    def get_tickets(self, eid): return [copy.deepcopy(t) for t in self.tickets.get(eid, [])]
    def latest_form(self, fid):
        versions = [v for (f, v) in self.forms if f == fid]
        return copy.deepcopy(self.forms[(fid, max(versions))]) if versions else None
    def get_form_version(self, fid, v): return copy.deepcopy(self.forms.get((fid, v)))
    def get_counters(self, keys):
        self.calls.append("get_counters")
        return {k: self.counters[k] for k in keys if k in self.counters}
    def get_member(self, email): return self.members.get(email)
    def get_membership_transactions(self, memberid): return self.txns.get(memberid, [])
    def get_site_config(self, key): return self.config.get(key, {})

    # registrations
    def get_registration(self, rid): return copy.deepcopy(self.regs.get(rid))
    def find_by_provider_id(self, pid): return next((copy.deepcopy(r) for r in self.regs.values() if r.get("providerPaymentId") == pid), None)
    def list_for_event(self, eid): return [copy.deepcopy(r) for r in self.regs.values() if r["eventId"] == eid]
    def list_by_email(self, email): return [copy.deepcopy(r) for r in self.regs.values() if r.get("email") == email and r.get("source") == "native"]
    def list_for_member(self, email):
        return [copy.deepcopy(r) for r in self.regs.values() if r.get("memberEmail") == email and r.get("source") == "native"]
    def list_expired_holds(self, iso_now):
        return [copy.deepcopy(r) for r in self.regs.values() if r["status"] == "pending_payment" and r.get("holdUntil", "9") <= iso_now]

    def _fits(self, ops):
        for key, n, limit in ops:
            if limit is not None and (n > limit or self.counters.get(key, 0) + n > limit):
                raise logic.CapacityFull("event" if key.startswith("EVENT#") else "ticket", key)

    def _apply(self, ops):
        for key, n, _ in ops:
            self.counters[key] = self.counters.get(key, 0) + n

    def reserve_and_create(self, reg, ops):
        with self.lock:
            if reg["registrationId"] in self.regs:
                raise logic.AlreadyExists()
            self._fits(ops)
            self._apply(ops)
            self.regs[reg["registrationId"]] = copy.deepcopy(reg)

    def create_waitlisted(self, reg):
        with self.lock:
            if reg["registrationId"] in self.regs:
                raise logic.AlreadyExists()
            self.regs[reg["registrationId"]] = copy.deepcopy(reg)

    def _write(self, rid, updates, remove, bump=False):
        reg = self.regs[rid]
        reg.update(copy.deepcopy(updates))
        for k in remove or []:
            reg.pop(k, None)
        if bump:
            reg["revision"] += 1
        return copy.deepcopy(reg)

    def update_registration(self, rid, updates, *, expect_status=None, expect_revision=None, bump=False, remove=None,
                            expect_absent=None):
        with self.lock:
            reg = self.regs.get(rid)
            if reg is None or (expect_status and reg["status"] != expect_status) or \
                    (expect_revision is not None and reg["revision"] != expect_revision) or \
                    (expect_absent and expect_absent in reg):
                raise logic.StateConflict()
            return self._write(rid, updates, remove, bump)

    def release(self, rid, expected_status, updates, ops, remove=None):
        with self.lock:
            reg = self.regs.get(rid)
            if reg is None or reg["status"] != expected_status:
                raise logic.StateConflict()
            if any(self.counters.get(k, 0) < n for k, n in ops):
                raise logic.StateConflict()
            for k, n in ops:
                self.counters[k] -= n
            return self._write(rid, updates, remove)

    def reserve_existing(self, rid, expected_status, updates, ops, remove=None):
        with self.lock:
            reg = self.regs.get(rid)
            if reg is None or reg["status"] != expected_status:
                raise logic.StateConflict()
            self._fits(ops)
            self._apply(ops)
            return self._write(rid, updates, remove)

    def rate_limit_allows(self, bucket, limit, window, now):
        key = (bucket, now // window)
        self.rl[key] = self.rl.get(key, 0) + 1
        return self.rl[key] <= limit

    # test helpers
    def add_event(self, **over):
        event = {"eventId": "e1", "slug": "picnic", "title": "Picnic", "status": "published", "source": "native",
                 "startsAt": "2026-10-10T10:00:00+09:00", "endsAt": "2026-10-10T16:00:00+09:00",
                 "access": {"guestsAllowed": True, "membersOnly": False, "requireRenewalCover": True},
                 "formId": "f1", "revision": 1, **over}
        self.events[event["eventId"]] = event
        form = event_model.new_form_version("f1", None, event_model.default_form_fields(), event_id=event["eventId"])
        self.forms[("f1", 1)] = form
        return event

    def add_tickets(self, event_id="e1", *tickets):
        self.tickets[event_id] = list(tickets)


class FakePayments:
    def __init__(self, fail=False):
        self.fail, self.created, self.cancelled = fail, [], []

    def is_configured(self): return True

    def create_checkout(self, *, reference_id, amount_yen, description, redirect_url):
        if self.fail:
            raise RuntimeError("square down")
        order = f"order-{len(self.created) + 1}"
        self.created.append({"reference_id": reference_id, "amount": amount_yen, "order": order, "redirect": redirect_url})
        return {"checkoutUrl": f"https://square.test/{order}", "providerPaymentId": order}

    def cancel_order(self, order_id): self.cancelled.append(order_id)

    def verify_webhook_signature(self, url, raw, signature): return signature == "sig"

    @staticmethod
    def parse_webhook_event(raw):
        import square as real_square
        return real_square.parse_webhook_event(raw)


ADULT = {"ticketTypeId": "adult", "eventId": "e1", "name": "Adult", "memberPriceJpy": 1000, "nonMemberPriceJpy": 1500,
         "sortOrder": 0, "active": True}
FREE = {"ticketTypeId": "free", "eventId": "e1", "name": "Child", "memberPriceJpy": 0, "nonMemberPriceJpy": 0,
        "sortOrder": 1, "active": True}
ACTIVE_MEMBER = {"loggedIn": True, "email": "m@example.com", "memberId": "M1",
                 "summary": {"validNow": True, "coversRenewal": True, "latestTo": "2027-03-31"}}
EXPIRED_MEMBER = {"loggedIn": True, "email": "x@example.com", "memberId": "M2",
                  "summary": {"validNow": False, "coversRenewal": False, "latestTo": "2025-03-31"}}
ANSWERS = {"fullName": "Ann Lee", "email": "ann@example.com", "phone": "+81 90-1234-5678",
           "consentPrivacy": True, "consentTerms": True}


class LinkedStore(FakeStore):
    """Registration store whose catalogue (events, tickets, forms) is an admin-side fake store's."""

    def __init__(self, admin_store):
        super().__init__()
        self.admin = admin_store
        admin_store.has_registrations = lambda eid: any(r["eventId"] == eid for r in self.regs.values())

    def get_event(self, eid): return self.admin.get_event(eid)
    def get_event_by_slug(self, slug): return next((e for e in self.admin.events.values() if e["slug"] == slug), None)
    def list_listed_events(self): return [e for e in self.admin.list_events() if e["status"] in ("published", "closed")]
    def get_tickets(self, eid): return self.admin.list_tickets(eid)
    def latest_form(self, fid): return self.admin.latest_form(fid)
    def get_form_version(self, fid, v): return self.admin.get_form_version(fid, v)

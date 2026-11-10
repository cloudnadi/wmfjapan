"""GET /events/{eventType}/availability — status derivation per section."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))
sys.path.insert(0, str(ROOT / "lambdas" / "get_event_availability"))
sys.path.insert(0, str(ROOT / "plugins" / "seat-selection" / "backend"))

os.environ.setdefault("REGION", "ap-northeast-1")
os.environ.setdefault("EVENTS_TABLE", "events-test")
os.environ.setdefault("DYNAMODB_TABLE", "registrations-test")
os.environ.setdefault("CELEBRITY_DINNER_CONFIG_TABLE", "celebrity-dinner-config-test")
os.environ.setdefault("CELEBRITY_DINNER_REGISTRATIONS_TABLE", "celebrity-dinner-registrations-test")

import handler  # noqa: E402


class CelebrityEventTests(unittest.TestCase):
    def section(self, registration, registered):
        with patch.object(handler, "count_celebrity_attendees", return_value=registered):
            return handler.celebrity_event_section("e", registration)

    def test_open_with_remaining(self):
        s = self.section({"celebrityCapacity": 100}, 40)
        self.assertEqual((s["status"], s["remainingSeats"]), ("open", 60))

    def test_full(self):
        s = self.section({"celebrityCapacity": 100}, 100)
        self.assertEqual((s["status"], s["registrationOpen"]), ("full", False))

    def test_unlimited_when_capacity_zero(self):
        s = self.section({}, 500)
        self.assertEqual((s["status"], s["remainingSeats"]), ("open", None))

    def test_manually_closed_and_deadline(self):
        self.assertEqual(self.section({"celebrityRegistrationClosed": True}, 0)["status"], "closed")
        self.assertEqual(self.section({"closesOn": "2000-01-01"}, 0)["status"], "closed")


class DinnerTests(unittest.TestCase):
    def section(self, info, config, booked):
        with patch.object(handler.dinner, "get_config", return_value=config), \
             patch.object(handler.dinner, "total_active_dinner_guest_count", return_value=booked):
            return handler.dinner_section("e", info)

    def test_not_offered(self):
        self.assertEqual(self.section({}, None, 0)["status"], "not_offered")

    def test_open_full_and_window(self):
        info = {"celebrityDinner": {"enabled": True}}
        self.assertEqual(self.section(info, {"maxTotalGuests": 50}, 20)["remainingSeats"], 30)
        self.assertEqual(self.section(info, {"maxTotalGuests": 50}, 50)["status"], "full")
        self.assertEqual(self.section(info, {"registrationEndsAt": "2000-01-01"}, 0)["status"], "closed")


class HandlerTests(unittest.TestCase):
    def call(self, **kw):
        return handler.lambda_handler({"httpMethod": "GET", "pathParameters": {"eventType": "e"}, **kw}, None)

    def test_404_unknown_event(self):
        with patch.object(handler, "get_event", return_value=None):
            self.assertEqual(self.call()["statusCode"], 404)

    def test_section_failure_is_isolated(self):
        item = {"registration": {"celebrityCapacity": 10}, "eventInfo": "{}"}
        with patch.object(handler, "get_event", return_value=item), \
             patch.object(handler, "count_celebrity_attendees", return_value=3), \
             patch.object(handler, "dinner_section", side_effect=RuntimeError("boom")):
            res = self.call()
        body = json.loads(res["body"])
        self.assertEqual(res["statusCode"], 200)
        self.assertEqual(body["celebrityEvent"]["remainingSeats"], 7)
        self.assertEqual(body["dinner"]["status"], "unavailable")
        self.assertEqual(body["seatSelection"]["status"], "not_offered")


if __name__ == "__main__":
    unittest.main()

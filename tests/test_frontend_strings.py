"""Every static t("...") key used by the event-platform UI exists in the English catalog."""

import json
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"
FILES = ("EventManager.jsx", "RichTextEditor.jsx", "FormBuilder.jsx", "DynamicForm.jsx", "EventShell.jsx", "EventsList.jsx",
         "EventPage.jsx", "EventRegister.jsx", "EventRegistered.jsx", "MyEventRegistrations.jsx", "EventRegistrations.jsx",
         "RegistrationTools.jsx", "CheckIn.jsx")
# Keys built at runtime: t(`events.<group>.${x}`) -> every value must exist.
DYNAMIC = {
    "events.status.": ("draft", "published", "unlisted", "closed", "archived"),
    "events.action.": ("draft", "published", "unlisted", "closed", "archived"),
    "events.layout.": ("classic", "hero", "minimal"),
    "events.sections.": ("agenda", "speakers", "sponsors", "faq", "contact"),
    "events.role.": ("super_admin", "event_manager"),
    "events.emails.": ("confirmation", "reminder"),
    "events.tab.": ("basics", "schedule", "location", "design", "tickets", "form", "access", "emails", "history"),
    "events.form.type.": ("text", "textarea", "email", "phone", "number", "dropdown", "radio", "checkbox", "multiselect",
                          "date", "file", "consent", "heading", "info", "group"),
    "events.form.op.": ("eq", "ne", "in", "notEmpty", "empty", "gt", "lt"),
    "events.form.mode.": ("build", "test"),
    "pub.list.": ("upcoming", "past"),
    "pub.sections.": ("agenda", "speakers", "sponsors", "faq", "contact"),
    "pub.status.": ("draft", "published", "closed", "unlisted", "archived", "confirmed", "pending_payment", "waitlisted", "cancelled"),
    "pub.step.": ("tickets", "details", "review"),
    "reg.payment.": ("none", "pending", "paid", "refund_pending", "refunding", "refunded"),
    "reg.via.": ("cash", "bank", "paypay", "square", "other"),
    "reg.add.mode.": ("normal", "ignore", "waitlist"),
    "reg.audience.": ("active", "confirmed", "pending", "waitlisted", "not_checked_in", "checked_in", "cancelled"),
    "reg.checkin.": ("already", "payment_due", "cancelled", "waitlisted", "wrong_event", "not_found", "not_checked_in"),
    "checkin.result.": ("ok", "undone", "already", "payment_due", "cancelled", "waitlisted", "wrong_event", "not_found", "not_checked_in"),
}


class FrontendStringTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((SRC / "locales" / "en.json").read_text(encoding="utf-8"))
        self.source = "".join((SRC / f).read_text(encoding="utf-8") for f in FILES)

    def test_static_keys_exist(self):
        keys = set(re.findall(r'\bt\("((?:events|pub|reg|checkin)\.[A-Za-z0-9_.]+)"', self.source))
        self.assertTrue(keys)
        self.assertEqual(sorted(k for k in keys if k not in self.catalog), [])

    def test_dynamic_keys_exist(self):
        missing = [p + v for p, values in DYNAMIC.items() for v in values if p + v not in self.catalog]
        self.assertEqual(missing, [])
        for layout in DYNAMIC["events.layout."]:
            self.assertIn(f"events.layout.{layout}.help", self.catalog)
        for status in DYNAMIC["events.status."]:
            self.assertIn(f"events.status.{status}.help", self.catalog)

    def test_ternary_keys_exist(self):
        for key in re.findall(r'"(events\.form\.core[A-Za-z]+)"', self.source):
            self.assertIn(key, self.catalog)
        for key in set(re.findall(r'"((?:pub|reg\.detail|checkin)\.[A-Za-z0-9_.]+)"', self.source)):
            if any(k.startswith(key + ".") for k in self.catalog):
                continue                                   # a prefix such as prefix="pub.status", not a key
            self.assertIn(key, self.catalog, key)

    def test_placeholders_in_strings_are_balanced(self):
        for key, text in self.catalog.items():
            self.assertEqual(text.count("{"), text.count("}"), key)


if __name__ == "__main__":
    unittest.main()

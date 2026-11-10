"""Registration email content."""

import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))
sys.path.insert(0, str(ROOT / "lambdas" / "qr_layer" / "python"))   # the same qrcode package the Lambda layer ships

import event_mailer as mailer  # noqa: E402

EVENT = {"eventId": "e1", "slug": "picnic", "title": "Autumn <Picnic>", "startsAt": "2026-10-10T10:00:00+09:00",
         "endsAt": "2026-10-11T16:00:00+09:00", "location": {"venueName": "Park", "address": "Tokyo"}}
REG = {"registrationId": "WMF-AAAA-BBBB", "fullName": "Ann <b>Lee</b>", "email": "a@example.com", "checkInToken": "tok",
       "tickets": [{"name": "Adult", "qty": 2, "subtotalJpy": 3000}], "totalJpy": 3000, "status": "confirmed",
       "paymentStatus": "paid", "paymentMethod": "square"}
METHODS = {"bank": {"instructions": "Account 123", "url": ""}, "paypay": {"instructions": "", "url": "https://paypay.test/x"}}


class BuildEmailTests(unittest.TestCase):
    def test_confirmed_default_has_details_and_qr(self):
        subject, text, html, qr = mailer.build_email("confirmed", REG, EVENT, METHODS)
        self.assertIn("confirmed", subject)
        for needle in ("WMF-AAAA-BBBB", "Adult × 2", "¥3,000", "2026-10-10 10:00 JST – 2026-10-11 16:00 JST", "Park, Tokyo"):
            self.assertIn(needle, text)
        self.assertEqual(qr, "WMFEV1.WMF-AAAA-BBBB.tok")
        self.assertIn("cid:qrcode", html)

    def test_html_escapes_registrant_and_admin_text(self):
        _, _, html, _ = mailer.build_email("confirmed", REG, EVENT, METHODS)
        self.assertNotIn("<b>Lee</b>", html)
        self.assertIn("Ann &lt;b&gt;Lee&lt;/b&gt;", html)
        self.assertIn("Autumn &lt;Picnic&gt;", html)

    def test_custom_template_and_placeholders(self):
        event = {**EVENT, "emails": {"confirmation": {"subject": "Hi {name} - {event_title}",
                                                      "body": "Ticket {ticket_type}, id {registration_id}, {unknown}"}}}
        subject, text, _, _ = mailer.build_email("confirmed", REG, event, METHODS)
        self.assertEqual(subject, "Hi Ann <b>Lee</b> - Autumn <Picnic>")
        self.assertTrue(text.startswith("Ticket Adult × 2, id WMF-AAAA-BBBB, {unknown}"))

    def test_blank_custom_template_falls_back_to_default(self):
        event = {**EVENT, "emails": {"confirmation": {"subject": "", "body": ""}}}
        self.assertIn("confirmed", mailer.build_email("confirmed", REG, event, METHODS)[0])

    def test_pending_payment_instructions(self):
        card = {**REG, "status": "pending_payment", "paymentStatus": "pending", "checkoutUrl": "https://square.test/o",
                "holdUntil": "2026-09-01T12:30:00+09:00"}
        _, text, _, qr = mailer.build_email("pending_payment", card, EVENT, METHODS)
        self.assertIsNone(qr)
        self.assertIn("Pay by card: https://square.test/o", text)
        self.assertIn("2026-09-01 12:30 JST", text)
        bank = {**card, "paymentMethod": "bank"}
        text = mailer.build_email("pending_payment", bank, EVENT, METHODS)[1]
        self.assertIn("Account 123", text)
        self.assertIn("WMF-AAAA-BBBB with your payment", text)
        self.assertIn("https://paypay.test/x", mailer.build_email("pending_payment", {**card, "paymentMethod": "paypay"}, EVENT, METHODS)[1])

    def test_other_kinds_and_no_qr_without_confirmation(self):
        for kind in ("waitlisted", "cancelled", "expired", "promoted"):
            subject, text, html, qr = mailer.build_email(kind, {**REG, "status": "pending_payment", "paymentStatus": "pending"}, EVENT, METHODS)
            self.assertTrue(subject and text and html)
            self.assertIsNone(qr)
        self.assertEqual(mailer.build_email("promoted", {**REG, "status": "confirmed"}, EVENT, METHODS)[3], "WMFEV1.WMF-AAAA-BBBB.tok")

    def test_mime_structure(self):
        subject, text, html, qr = mailer.build_email("confirmed", REG, EVENT, METHODS)
        mime = mailer.make_mime(subject, text, html, qr, "a@example.com", "noreply@example.com")
        self.assertEqual((mime["To"], mime["From"]), ("a@example.com", "noreply@example.com"))
        self.assertIn("multipart/alternative", mime.as_string())

    def test_qr_png_is_a_valid_square_png(self):
        import struct
        import zlib
        png = mailer.qr_png("WMFEV1.WMF-AAAA-BBBB.tok")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height, depth, color = struct.unpack(">IIBB", png[16:26])
        self.assertEqual((width == height, depth, color), (True, 8, 0))
        self.assertGreaterEqual(width, 150)
        idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8]
        raw = zlib.decompress(idat)
        self.assertEqual(len(raw), height * (width + 1))
        y, x0 = 3 * 8 + 4, 3 * 8                                  # inside the top-left finder pattern (border 3, scale 8)
        row = raw[y * (width + 1) + 1:(y + 1) * (width + 1)]
        self.assertEqual(set(row[x0:x0 + 7 * 8]), {0})            # 7 modules of dark: the finder's outer ring
        self.assertEqual(set(row[:x0]), {255})                    # white quiet zone
        self.assertNotEqual(png, mailer.qr_png("something else"))

    def test_mime_embeds_the_qr_image(self):
        subject, text, html, qr = mailer.build_email("confirmed", REG, EVENT, METHODS)
        mime = mailer.make_mime(subject, text, html, qr, "a@example.com", "noreply@example.com")
        self.assertIn("Content-ID: <qrcode>", mime.as_string())

    def test_missing_date_fallback(self):
        self.assertEqual(mailer._event_date({}), "To be announced")


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.patch = unittest.mock.patch.object(mailer, "FRONTEND_URL", "https://site.test")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def html(self, kind="confirmed", reg=REG, event=EVENT):
        return mailer.build_email(kind, reg, event, METHODS)[2]

    def test_every_kind_uses_the_branded_frame(self):
        for kind in mailer.DEFAULTS:
            html = self.html(kind)
            for needle in ("https://site.test/logo.png", "WMF Japan", "info@wmfjapan.com", "#1c2722", "Autumn &lt;Picnic&gt;"):
                self.assertIn(needle, html, f"{kind}: {needle}")

    def test_summary_card_and_event_link(self):
        html = self.html()
        for needle in ("Registration summary", "WMF-AAAA-BBBB", "¥3,000", "Park, Tokyo", "https://site.test/events/picnic", "View event details"):
            self.assertIn(needle, html)

    def test_payment_card_for_unpaid_registrations(self):
        reg = {**REG, "status": "pending_payment", "paymentStatus": "pending", "paymentMethod": "square",
               "checkoutUrl": "https://square.test/pay", "holdUntil": "2026-10-10T10:00:00+09:00"}
        html = self.html("pending_payment", reg)
        self.assertIn("Pay by card", html)
        self.assertIn('href="https://square.test/pay"', html)
        self.assertNotIn("cid:qrcode", html)
        bad = {**reg, "checkoutUrl": "javascript:alert(1)"}
        self.assertNotIn("javascript:", self.html("pending_payment", bad).replace("Pay by card: javascript:alert(1)", ""))

    def test_accent_colour_is_validated(self):
        ok = self.html(event={**EVENT, "design": {"accentColor": "#112233"}})
        self.assertIn("border:2px solid #112233", ok)
        evil = self.html(event={**EVENT, "design": {"accentColor": "red;background:url(x)"}})
        self.assertIn("border:2px solid #69a785", evil)
        self.assertNotIn("url(x)", evil)

    def test_bulk_messages_are_branded_and_escaped(self):
        plain, html = mailer.build_message("Hello <script>x</script>\n\nSecond paragraph")
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("logo.png", html)
        self.assertIn("Hello <script>x</script>", plain)

    def test_mime_still_carries_text_html_and_qr(self):
        subject, text, html, qr = mailer.build_email("confirmed", REG, EVENT, METHODS)
        message = mailer.make_mime(subject, text, html, qr, "a@example.com", "from@example.com").as_string()
        self.assertIn("text/plain", message)
        self.assertIn("text/html", message)
        self.assertIn("Content-ID: <qrcode>", message)


if __name__ == "__main__":
    unittest.main()

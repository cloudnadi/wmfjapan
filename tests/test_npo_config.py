"""NPO membership settings: the fee and the per-year WhatsApp group links."""

import importlib.util
import json
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.update(REGION="ap-northeast-1", SITE_CONFIG_TABLE="site", ASSETS_BUCKET="b", ASSETS_BASE_URL="https://assets.test")

spec = importlib.util.spec_from_file_location("manage_site_config_handler", ROOT / "lambdas/manage_site_config/handler.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)


def normalize(**config):
    return handler.normalize_npo_config(json.dumps(config))


class NpoConfigTests(unittest.TestCase):
    def test_fee_alone_still_works_and_has_no_links(self):
        self.assertEqual(normalize(fee=1000), {"fee": 1000, "whatsappLinks": {}})

    def test_links_are_kept_per_year_and_blanks_dropped(self):
        result = normalize(fee=1000, whatsappLinks={
            "2026-27": " https://chat.whatsapp.com/ABC123 ", "2025-26": "https://chat.whatsapp.com/OLD", "2027-28": "  ",
        })
        self.assertEqual(result["whatsappLinks"], {
            "2026-27": "https://chat.whatsapp.com/ABC123", "2025-26": "https://chat.whatsapp.com/OLD",
        })

    def test_bad_links_and_years_are_rejected(self):
        for links in ({"2026-27": "http://chat.whatsapp.com/x"}, {"2026-27": "javascript:alert(1)"},
                      {"2026-27": "https://a.test/x y"}, {"2026": "https://chat.whatsapp.com/x"},
                      {"next year": "https://chat.whatsapp.com/x"}, ["https://chat.whatsapp.com/x"]):
            with self.assertRaises(ValueError, msg=links):
                normalize(fee=1000, whatsappLinks=links)


if __name__ == "__main__":
    unittest.main()

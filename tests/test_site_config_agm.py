"""Public site config exposes only the soonest open, upcoming AGM."""

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
os.environ.update(REGION="ap-northeast-1", SITE_CONFIG_TABLE="site", AGMS_TABLE="agms")

spec = importlib.util.spec_from_file_location("get_site_config_handler", ROOT / "lambdas/get_site_config/handler.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)


class OpenAgmTests(unittest.TestCase):
    def open_agm(self, items):
        table = mock.Mock()
        table.scan.return_value = {"Items": items}
        resource = mock.Mock()
        resource.Table.return_value = table
        with mock.patch.object(handler, "dynamodb", resource):
            return handler.get_open_agm()

    def test_none_when_closed_or_past(self):
        self.assertIsNone(self.open_agm([]))
        self.assertIsNone(self.open_agm([{"agmId": "a", "date": "2099-01-01", "enabled": False}]))
        self.assertIsNone(self.open_agm([{"agmId": "a", "date": "2020-01-01", "enabled": True}]))

    def test_picks_soonest_open_and_hides_internal_fields(self):
        agm = self.open_agm([
            {"agmId": "later", "date": "2099-12-01", "enabled": True, "title": "Later"},
            {"agmId": "soon", "date": "2099-06-01", "enabled": True, "title": "Soon", "createdAt": "x", "enabled_by": "y"},
        ])
        self.assertEqual(agm["agmId"], "soon")
        self.assertNotIn("createdAt", agm)
        self.assertNotIn("enabled", agm)


if __name__ == "__main__":
    unittest.main()

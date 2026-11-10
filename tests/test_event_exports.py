"""Export columns, CSV and Excel output."""

import io
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))

import event_exports as x  # noqa: E402
import event_model as m  # noqa: E402

FORM1 = {"version": 1, "fields": m.default_form_fields() + [{"key": "diet", "type": "text", "label": "Dietary"},
                                                           {"key": "old", "type": "text", "label": "Old question"}]}
FORM2 = {"version": 2, "fields": m.default_form_fields() + [
    {"key": "diet", "type": "text", "label": "Dietary needs"},
    {"key": "h", "type": "heading", "label": "Heading"},
    {"key": "guests", "type": "group", "label": "Guests", "maxRepeat": 3, "fields": [{"key": "gname", "type": "text", "label": "Name"}]},
    {"key": "photo", "type": "file", "label": "Photo", "validation": {"maxSizeMb": 1, "allowedTypes": ["image/png"]}},
    {"key": "tags", "type": "multiselect", "label": "Tags", "options": [{"value": "a", "label": "A"}]}]}
TICKETS = [{"ticketTypeId": "a", "name": "Adult"}, {"ticketTypeId": "c", "name": "Child"}]
REG = {"registrationId": "NK-AAAA-BBBB", "status": "confirmed", "paymentStatus": "paid", "paymentMethod": "bank", "totalJpy": 3000,
       "seats": 3, "createdAt": "2026-09-01T12:00:00+09:00", "checkedInAt": "2026-10-10T18:05:00+09:00",
       "tickets": [{"ticketTypeId": "a", "name": "Adult", "qty": 2}, {"ticketTypeId": "c", "name": "Child", "qty": 1}],
       "answers": {"fullName": "Ann Lee", "email": "a@b.co", "diet": "vegan", "old": "legacy answer",
                   "guests": [{"gname": "Bo"}, {"gname": "Cy"}], "photo": {"fileId": "f", "name": "me.png", "size": 1, "contentType": "image/png"},
                   "tags": ["a", "b"], "consentPrivacy": True, "removedField": "orphan"}}


class ColumnTests(unittest.TestCase):
    def test_columns_follow_newest_form_then_older_then_orphans(self):
        cols = x.answer_columns([FORM1, FORM2], [REG])
        keys = [c["key"] for c in cols]
        self.assertEqual(keys[:len(m.CORE_KEYS)], list(m.CORE_KEYS))
        self.assertEqual(keys[len(m.CORE_KEYS):], ["diet", "guests", "photo", "tags", "old", "removedField"])
        labels = {c["key"]: c["label"] for c in cols}
        self.assertEqual((labels["diet"], labels["old"], labels["removedField"]), ("Dietary needs", "Old question", "removedField"))
        self.assertNotIn("h", keys)                                   # display-only fields are not columns
        self.assertNotIn("guests.gname", keys)

    def test_cells_flatten_every_answer_shape(self):
        self.assertEqual(x.cell(REG, "a.guests"), "gname=Bo | gname=Cy")
        self.assertEqual(x.cell(REG, "a.photo"), "me.png")
        self.assertEqual(x.cell(REG, "a.tags"), "a | b")
        self.assertEqual(x.cell(REG, "a.consentPrivacy"), "Yes")
        self.assertEqual(x.cell(REG, "a.missing"), "")
        self.assertEqual(x.cell(REG, "createdAt"), "2026-09-01 12:00")
        self.assertEqual(x.cell({}, "checkedInAt"), "")
        self.assertEqual(x.cell(REG, "totalJpy"), "3000")

    def test_table_has_ticket_columns_and_one_row_per_registration(self):
        cols = x.answer_columns([FORM2], [REG])
        rows = x.export_table([REG, {**REG, "registrationId": "NK-2", "tickets": [{"ticketTypeId": "c", "name": "Child", "qty": 4}]}], cols, TICKETS)
        header = rows[0]
        self.assertEqual(len(rows), 3)
        self.assertEqual(header[:3], ["Registration ID", "Status", "Payment status"])
        self.assertIn("Adult (qty)", header)
        i_adult, i_child = header.index("Adult (qty)"), header.index("Child (qty)")
        self.assertEqual((rows[1][i_adult], rows[1][i_child]), ("2", "1"))
        self.assertEqual((rows[2][i_adult], rows[2][i_child]), ("", "4"))
        self.assertTrue(all(len(r) == len(header) for r in rows))

    def test_sort_values(self):
        regs = [{"totalJpy": 900, "answers": {"n": "10"}}, {"totalJpy": 1000, "answers": {"n": "9"}}, {"totalJpy": 50, "answers": {"n": "abc"}}]
        self.assertEqual([r["totalJpy"] for r in sorted(regs, key=lambda r: x.sort_value(r, "totalJpy"))], [50, 900, 1000])
        self.assertEqual([r["answers"]["n"] for r in sorted(regs, key=lambda r: x.sort_value(r, "a.n"))], ["9", "10", "abc"])


class CsvTests(unittest.TestCase):
    def test_csv_round_trip_with_bom_unicode_and_quotes(self):
        import csv
        data = x.to_csv([["Name", "Note"], ["山田 太郎", 'He said "hi", twice'], ["മലയാളം", "line1\nline2"]])
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(rows[1], ["山田 太郎", 'He said "hi", twice'])
        self.assertEqual(rows[2], ["മലയാളം", "line1\nline2"])

    def test_formula_injection_is_neutralised(self):
        import csv
        evil = ["=HYPERLINK(\"http://evil\")", "+1+1", "-2", "@SUM(A1)", "\t=1", "ok", "a=b", "0-1"]
        rows = list(csv.reader(io.StringIO(x.to_csv([evil]).decode("utf-8-sig"))))
        self.assertEqual(rows[0][:5], ["'" + e for e in evil[:5]])
        self.assertEqual(rows[0][5:], ["ok", "a=b", "0-1"])


class XlsxTests(unittest.TestCase):
    def test_opens_in_openpyxl_with_all_values_as_text(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl is not installed")
        rows = [["Name", "Qty", "Formula?"], ["山田 <&> \"x\"", "5", "=1+1"], ["", "മലയാളം", "line1\nline2"], ["ctl\x01char", "007", "-3"]]
        wb = openpyxl.load_workbook(io.BytesIO(x.to_xlsx(rows)))
        ws = wb.active
        self.assertEqual(ws.title, "Registrations")
        got = [[c.value for c in row] for row in ws.iter_rows()]
        self.assertEqual(got[0], rows[0])
        self.assertEqual(got[1], ["山田 <&> \"x\"", "5", "=1+1"])
        self.assertEqual(got[3], ["ctlchar", "007", "-3"])           # control chars stripped, leading zeros kept
        self.assertEqual(ws["B2"].data_type, "s")
        self.assertEqual(ws["C2"].data_type, "s")                    # "=1+1" stays a string, not a formula
        self.assertTrue(ws["A1"].font.b)
        self.assertEqual(ws.freeze_panes, "A2")

    def test_structure_without_openpyxl(self):
        import zipfile
        data = x.to_xlsx([["a", "b"], ["1", "2"]], sheet_name="Bad[name]:*?")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIsNone(z.testzip())
            self.assertIn('name="Badname"', z.read("xl/workbook.xml").decode())
            self.assertIn("xl/worksheets/sheet1.xml", z.namelist())

    def test_empty_and_wide_tables(self):
        self.assertTrue(x.to_xlsx([]))
        self.assertEqual(x._col_name(0), "A")
        self.assertEqual(x._col_name(25), "Z")
        self.assertEqual(x._col_name(26), "AA")
        self.assertEqual(x._col_name(701), "ZZ")
        self.assertEqual(x._col_name(702), "AAA")


class FilenameTests(unittest.TestCase):
    def test_filename_is_safe(self):
        now = datetime(2026, 10, 1, 9, 5, tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(x.export_filename({"slug": "autumn-dinner"}, "csv", now), "registrations-autumn-dinner-20261001-0905.csv")
        self.assertEqual(x.export_filename({"slug": "../../etc/passwd"}, "xlsx", now), "registrations-etcpasswd-20261001-0905.xlsx")


if __name__ == "__main__":
    unittest.main()

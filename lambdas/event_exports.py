"""Registration table columns and CSV / Excel export (pure Python, no third-party libraries)."""

import csv
import io
import re
import zipfile
from datetime import datetime
from html import escape
from typing import Any

import event_model

FIXED_COLUMNS = [
    {"id": "registrationId", "label": "Registration ID"},
    {"id": "status", "label": "Status"},
    {"id": "paymentStatus", "label": "Payment status"},
    {"id": "paymentMethod", "label": "Payment method"},
    {"id": "totalJpy", "label": "Total (JPY)", "type": "number"},
    {"id": "seats", "label": "Tickets", "type": "number"},
    {"id": "createdAt", "label": "Registered at (JST)"},
    {"id": "checkedInAt", "label": "Checked in at (JST)"},
]
DISPLAY_ONLY = ("heading", "info")
INJECTION_RE = re.compile(r"^[=+\-@\t\r]")


def answer_columns(forms: list, regs: list) -> list:
    """One column per form field (label from the newest version that has it), then any leftover answer keys."""
    columns, seen = [], set()
    for form in sorted(forms, key=lambda f: f["version"], reverse=True):
        for path, field in event_model.flatten_fields(form["fields"]).items():
            if field.get("type") in DISPLAY_ONLY or "." in path or path in seen:
                continue
            seen.add(path)
            columns.append({"id": f"a.{path}", "label": field["label"], "type": field["type"], "key": path, "order": form["version"]})
    # Keep schema order of the newest version first, then older-only fields.
    newest = max((f["version"] for f in forms), default=0)
    newest_keys = [p for p in event_model.flatten_fields(next((f for f in forms if f["version"] == newest), {"fields": []})["fields"]) if "." not in p]
    columns.sort(key=lambda c: (newest_keys.index(c["key"]) if c["key"] in newest_keys else len(newest_keys) + 1 - c["order"] / 1000))
    for reg in regs:
        for key in reg.get("answers", {}):
            if key not in seen:
                seen.add(key)
                columns.append({"id": f"a.{key}", "label": key, "type": "text", "key": key})
    return [{k: v for k, v in c.items() if k != "order"} for c in columns]


def tickets_summary(reg: dict) -> str:
    return ", ".join(f"{l['name']} × {l['qty']}" for l in reg.get("tickets", []))


def _jst(value: Any) -> str:
    parsed = event_model.parse_iso(value) if value else None
    return parsed.astimezone(event_model.JST).strftime("%Y-%m-%d %H:%M") if parsed else ""


def _flatten(value: Any) -> str:
    """Human-readable text for any answer shape (lists, files, repeatable groups)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return " | ".join(_flatten(v) for v in value)
    if isinstance(value, dict):
        if "fileId" in value:
            return str(value.get("name", "file"))
        return "; ".join(f"{k}={_flatten(v)}" for k, v in value.items())
    return str(value)


def cell(reg: dict, column_id: str) -> str:
    if column_id.startswith("a."):
        return _flatten(reg.get("answers", {}).get(column_id[2:]))
    if column_id == "seats":
        return str(reg.get("seats", ""))
    if column_id in ("createdAt", "checkedInAt"):
        return _jst(reg.get(column_id))
    if column_id == "totalJpy":
        return str(reg.get("totalJpy", ""))
    return str(reg.get(column_id, "") if reg.get(column_id) is not None else "")


def sort_value(reg: dict, column_id: str):
    if column_id in ("totalJpy", "seats"):
        return int(reg.get(column_id) or 0)
    if column_id.startswith("a."):
        raw = reg.get("answers", {}).get(column_id[2:])
        text = _flatten(raw)
        try:
            return (0, float(text))
        except ValueError:
            return (1, text.lower())
    return str(reg.get(column_id) or "").lower()


def ticket_columns(tickets: list) -> list:
    return [{"id": f"t.{t['ticketTypeId']}", "label": f"{t['name']} (qty)", "type": "number"} for t in tickets]


def export_table(regs: list, columns: list, tickets: list) -> list:
    """Header row + one row per registration. Ticket quantities get their own columns."""
    ticket_cols = ticket_columns(tickets)
    header = [c["label"] for c in FIXED_COLUMNS] + [c["label"] for c in ticket_cols] + [c["label"] for c in columns]
    rows = [header]
    for reg in regs:
        qty = {l["ticketTypeId"]: l["qty"] for l in reg.get("tickets", [])}
        rows.append([cell(reg, c["id"]) for c in FIXED_COLUMNS]
                    + [str(qty.get(c["id"][2:], "")) for c in ticket_cols]
                    + [cell(reg, c["id"]) for c in columns])
    return rows


def csv_safe(text: str) -> str:
    """Stop spreadsheet apps treating user text as a formula (=, +, -, @ at the start)."""
    return "'" + text if INJECTION_RE.match(text) else text


def to_csv(rows: list) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in rows:
        writer.writerow([csv_safe(str(c)) for c in row])
    return ("﻿" + buffer.getvalue()).encode("utf-8")        # BOM: Excel opens UTF-8 (Japanese, Malayalam) correctly


def _col_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def to_xlsx(rows: list, sheet_name: str = "Registrations") -> bytes:
    """A minimal .xlsx. Every cell is an inline string, so nothing is ever evaluated as a formula."""
    sheet_rows = []
    for r, row in enumerate(rows, start=1):
        style = ' s="1"' if r == 1 else ""
        cells = "".join(
            f'<c r="{_col_name(c)}{r}" t="inlineStr"{style}><is><t xml:space="preserve">{escape(_CONTROL.sub("", str(v)))}</t></is></c>'
            for c, v in enumerate(row))
        sheet_rows.append(f'<row r="{r}">{cells}</row>')
    widths = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{min(40, max(10, max(len(str(r[i])) for r in rows[:200] if i < len(r)) + 2))}" customWidth="1"/>'
                     for i in range(len(rows[0]))) if rows and rows[0] else ""
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             f'<cols>{widths}</cols><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    name = escape(re.sub(r"[\[\]:*?/\\]", "", sheet_name)[:31] or "Sheet1")
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        "xl/worksheets/sheet1.xml": sheet,
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for filename, content in files.items():
            z.writestr(filename, content)
    return out.getvalue()


def export_filename(event: dict, extension: str, now: datetime) -> str:
    slug = re.sub(r"[^a-z0-9-]", "", str(event.get("slug", "event")).lower()) or "event"
    return f"registrations-{slug}-{now.strftime('%Y%m%d-%H%M')}.{extension}"

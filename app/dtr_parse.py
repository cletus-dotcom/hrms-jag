"""
Parse biometric attlog .dat exports for DTR upload.

Supports:
- ZKTeco-style tab-separated lines: PIN, datetime, verified, status, ...
  Status 0–3: check-in, check-out, break-out, break-in (same mapping as fixed format).
- Legacy fixed-width lines where punch type sits at 1-based column 38 (index 37).

Filenames like 1_attlog.dat, 58_attlog.dat, AECG214960162_attlog.dat only affect
logging; employee IDs come from each data line.
"""
from __future__ import annotations

import re
from collections import defaultdict


def _parse_date_and_time_from_zk_field(col1: str) -> tuple[str | None, str]:
    """Parse ZKTeco datetime field: 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD HH:MM'."""
    s = (col1 or "").strip()
    if not s:
        return None, ""
    if " " in s:
        date_part, time_part = s.split(None, 1)
    elif "T" in s:
        date_part, time_part = s.split("T", 1)
    else:
        date_part, time_part = s, ""
    date_part = date_part.strip()
    time_part = time_part.strip()
    dt_str = None
    if len(date_part) >= 10 and date_part[4] == "-" and date_part[7] == "-":
        dt_str = date_part[:10]
    elif len(date_part) >= 8 and date_part[:8].isdigit():
        dt_str = date_part[:4] + "-" + date_part[4:6] + "-" + date_part[6:8]
    if not dt_str:
        return None, ""
    if not time_part:
        return dt_str, ""
    tparts = time_part.split(":")
    if len(tparts) >= 2:
        try:
            h, m = int(tparts[0]), int(tparts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return dt_str, f"{h:02d}:{m:02d}"
        except ValueError:
            pass
    return dt_str, ""


def _parse_time_from_fixed_line(raw_line: str) -> str:
    """Time from columns 27–31 (index 26:31) on fixed-width exports."""
    if not raw_line or len(raw_line) < 31:
        return ""
    t = raw_line[26:31].strip()
    digits = "".join(ch for ch in t if ch.isdigit())
    if len(digits) >= 4:
        return digits[0:2] + ":" + digits[2:4]
    return ""


def _parse_date_from_fixed_start(raw_line: str) -> str | None:
    """Date from start of fixed-width line: YYYY-MM-DD or YYYYMMDD."""
    s = raw_line.strip()
    if len(s) < 8:
        return None
    raw = s[0:10] if len(s) >= 10 else s[0:8]
    if "-" in raw:
        return raw[0:10] if len(raw) >= 10 else None
    if len(raw) >= 8 and raw[:8].isdigit():
        return raw[0:4] + "-" + raw[4:6] + "-" + raw[6:8]
    return None


def _split_delimited_line(ln: str) -> list[str] | None:
    """Split tab- or comma-delimited attlog line; None if not delimited."""
    if "\t" in ln:
        return ln.split("\t")
    if "," in ln and ln.count(",") >= 3:
        return ln.split(",")
    return None


def _try_parse_tab_or_csv_line(ln: str) -> dict | None:
    """
    ZKTeco tab/CSV: col0 PIN, col1 datetime, col3 status (0–3).
    Verified (col2) may be 1, 15, etc.
    """
    parts = _split_delimited_line(ln)
    if not parts or len(parts) < 4:
        return None
    emp_id = (parts[0] or "").strip()
    if not emp_id:
        return None
    dt_str, tm = _parse_date_and_time_from_zk_field(parts[1] or "")
    if not dt_str:
        return None
    try:
        status = int((parts[3] or "").strip())
    except ValueError:
        return None
    if status not in (0, 1, 2, 3):
        return None
    if not tm:
        tm = _parse_time_from_fixed_line(ln)  # unlikely fallback
    return {"emp_id": emp_id, "date": dt_str, "col38": status, "time": tm}


def _try_parse_fixed_width_line(ln: str) -> dict | None:
    """Legacy fixed-width: event digit at index 37."""
    if len(ln) <= 37:
        return None
    event_char = ln[37]
    try:
        col38 = int(event_char) if event_char.isdigit() else -1
    except ValueError:
        return None
    if col38 not in (0, 1, 2, 3):
        return None
    if "\t" in ln or ("," in ln and ln.count(",") >= 3):
        return None
    parts = ln.split()
    emp_id = (parts[0] or "").strip() if parts else ""
    if not emp_id:
        return None
    col2 = (parts[1] or "").strip() if len(parts) > 1 else ln
    dt_str = None
    if len(col2) >= 8:
        raw = col2[0:10] if len(col2) >= 10 else col2[0:8]
        if "-" in raw:
            dt_str = raw[0:10]
        elif len(raw) >= 8 and raw[:8].isdigit():
            dt_str = raw[0:4] + "-" + raw[4:6] + "-" + raw[6:8]
    if not dt_str:
        dt_str = _parse_date_from_fixed_start(ln)
    if not dt_str:
        return None
    tm = _parse_time_from_fixed_line(ln)
    return {"emp_id": emp_id, "date": dt_str, "col38": col38, "time": tm}


def parse_dtr_dat_file(file_content: bytes | str, filename: str = "") -> list[dict]:
    """
    Parse DTR .dat content. Returns sorted list of dicts:
    employee_id, date (YYYY-MM-DD), check_in, break_out, break_in, check_out (HH:MM).

    ``filename`` is accepted for API stability (any * _attlog.dat name is supported).
    """
    _ = filename
    text = (
        file_content.decode("utf-8-sig", errors="replace")
        if isinstance(file_content, bytes)
        else file_content
    )
    lines = [ln.rstrip("\r\n") for ln in text.splitlines() if ln.strip()]

    rows_raw: list[dict] = []
    for ln in lines:
        rec = _try_parse_tab_or_csv_line(ln)
        if rec is None:
            rec = _try_parse_fixed_width_line(ln)
        if rec is None:
            continue
        rows_raw.append(rec)

    groups: dict = defaultdict(
        lambda: {"check_in": "", "break_out": "", "break_in": "", "check_out": ""}
    )
    for r in rows_raw:
        key = (r["emp_id"], r["date"])
        t = r["time"]
        c = r["col38"]
        if c == 0:
            groups[key]["check_in"] = t
        elif c == 1:
            groups[key]["check_out"] = t
        elif c == 2:
            groups[key]["break_out"] = t
        elif c == 3:
            groups[key]["break_in"] = t

    out = []
    for (emp_id, dt_str), times in groups.items():
        out.append(
            {
                "employee_id": emp_id,
                "date": dt_str,
                "check_in": times["check_in"],
                "break_out": times["break_out"],
                "break_in": times["break_in"],
                "check_out": times["check_out"],
            }
        )
    out.sort(key=lambda x: (x["employee_id"], x["date"]))
    return out

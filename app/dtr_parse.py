"""
Parse biometric attlog .dat exports for DTR upload.

Supports:
- ZKTeco-style tab-separated lines: PIN, datetime, verified, status, ...
  Status 0–3: check-in, check-out, break-out, break-in (same mapping as fixed format).
- Legacy fixed-width lines where punch type sits at 1-based column 38 (index 37).

Filenames like 1_attlog.dat, 58_attlog.dat, AECG214960162_attlog.dat only affect
logging; employee IDs come from each data line.

Each employee-day row keeps the four CSC slots (last punch wins per slot) and also
retains every accepted raw punch for duplicate-slot review.
"""
from __future__ import annotations

from collections import defaultdict

# ZKTeco / fixed-width event code → CSC Form 48 slot name
EVENT_CODE_TO_SLOT = {
    0: "am_in",       # check-in
    1: "pm_out",      # check-out
    2: "am_out",      # break-out
    3: "pm_in",       # break-in
}

SLOT_TO_ROW_FIELD = {
    "am_in": "check_in",
    "am_out": "break_out",
    "pm_in": "break_in",
    "pm_out": "check_out",
}


def _parse_date_and_time_from_zk_field(col1: str) -> tuple[str | None, str, str]:
    """
    Parse ZKTeco datetime field: 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD HH:MM'.
    Returns (date_yyyy_mm_dd, time_hh_mm, source_timestamp_raw).
    """
    s = (col1 or "").strip()
    if not s:
        return None, "", ""
    if " " in s:
        date_part, time_part = s.split(None, 1)
    elif "T" in s:
        date_part, time_part = s.split("T", 1)
    else:
        date_part, time_part = s, ""
    date_part = date_part.strip()
    time_part = time_part.strip()
    source_ts = s
    dt_str = None
    if len(date_part) >= 10 and date_part[4] == "-" and date_part[7] == "-":
        dt_str = date_part[:10]
    elif len(date_part) >= 8 and date_part[:8].isdigit():
        dt_str = date_part[:4] + "-" + date_part[4:6] + "-" + date_part[6:8]
    if not dt_str:
        return None, "", ""
    if not time_part:
        return dt_str, "", source_ts
    tparts = time_part.split(":")
    if len(tparts) >= 2:
        try:
            h, m = int(tparts[0]), int(tparts[1])
            sec = int(tparts[2]) if len(tparts) >= 3 else 0
            if 0 <= h <= 23 and 0 <= m <= 59 and 0 <= sec <= 59:
                if len(tparts) >= 3:
                    return dt_str, f"{h:02d}:{m:02d}:{sec:02d}", source_ts
                return dt_str, f"{h:02d}:{m:02d}", source_ts
        except ValueError:
            pass
    return dt_str, "", source_ts


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
    dt_str, tm, source_ts = _parse_date_and_time_from_zk_field(parts[1] or "")
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
    return {
        "emp_id": emp_id,
        "date": dt_str,
        "col38": status,
        "time": tm,
        "source_timestamp": source_ts or (f"{dt_str} {tm}".strip() if tm else dt_str),
    }


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
    source_ts = f"{dt_str} {tm}".strip() if tm else dt_str
    return {
        "emp_id": emp_id,
        "date": dt_str,
        "col38": col38,
        "time": tm,
        "source_timestamp": source_ts,
    }


def duplicate_slots_from_punches(punches: list[dict]) -> dict[str, list[dict]]:
    """
    Group punches by slot; return only slots that have 2+ punches
    (including identical repeated timestamps).
    """
    by_slot: dict[str, list[dict]] = defaultdict(list)
    for p in punches or []:
        slot = (p.get("slot") or "").strip()
        if slot in EVENT_CODE_TO_SLOT.values():
            by_slot[slot].append(p)
    return {slot: items for slot, items in by_slot.items() if len(items) >= 2}


def parse_dtr_dat_file(file_content: bytes | str, filename: str = "") -> list[dict]:
    """
    Parse DTR .dat content. Returns sorted list of dicts:
    employee_id, date (YYYY-MM-DD), check_in, break_out, break_in, check_out (HH:MM or HH:MM:SS),
    punches (list of raw punch dicts in source order).

    Slot selection uses last-wins per (employee, date, event). Duplicate punches for
    the same slot are retained in ``punches`` for HR review.

    Times keep seconds when the source datetime includes them (HH:MM:SS).

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
    for line_idx, ln in enumerate(lines, start=1):
        rec = _try_parse_tab_or_csv_line(ln)
        if rec is None:
            rec = _try_parse_fixed_width_line(ln)
        if rec is None:
            continue
        rec["line_no"] = line_idx
        rows_raw.append(rec)

    groups: dict = defaultdict(
        lambda: {
            "check_in": "",
            "break_out": "",
            "break_in": "",
            "check_out": "",
            "punches": [],
        }
    )
    for r in rows_raw:
        key = (r["emp_id"], r["date"])
        t = r["time"]
        c = r["col38"]
        slot = EVENT_CODE_TO_SLOT.get(c)
        if not slot:
            continue
        punch = {
            "event_code": c,
            "slot": slot,
            "time": t,
            "source_timestamp": (r.get("source_timestamp") or "").strip() or t,
            "line_no": r.get("line_no") or 0,
            "sequence_no": len(groups[key]["punches"]) + 1,
        }
        groups[key]["punches"].append(punch)
        field = SLOT_TO_ROW_FIELD[slot]
        # Last punch wins for the selected CSC slot value
        if t:
            groups[key][field] = t

    out = []
    for (emp_id, dt_str), times in groups.items():
        punches = times["punches"]
        dup_slots = duplicate_slots_from_punches(punches)
        out.append(
            {
                "employee_id": emp_id,
                "date": dt_str,
                "check_in": times["check_in"],
                "break_out": times["break_out"],
                "break_in": times["break_in"],
                "check_out": times["check_out"],
                "punches": punches,
                "duplicate_slots": sorted(dup_slots.keys()),
                "has_duplicate_punches": bool(dup_slots),
            }
        )
    out.sort(key=lambda x: (x["employee_id"], x["date"]))
    return out

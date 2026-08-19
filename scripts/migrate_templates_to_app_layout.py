#!/usr/bin/env python3
"""Migrate legacy base.html templates to layouts/app.html (excludes landing & login)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"

SKIP = {
    "landing.html",
    "login.html",
    "dashboard.html",
    "employee_dashboard.html",
    "layouts/app.html",
    "base.html",
}

# navbar_title, breadcrumb labels after Home (last = current page, no url)
PAGE_META: dict[str, tuple[str, list[str]]] = {
    "users/list.html": ("Manage Users", ["Administration", "Manage Users"]),
    "users/form.html": ("'Edit User' if user else 'Add User'", ["Administration", "Users", "'Edit' if user else 'Add'"]),
    "employees/list.html": ("Manage Employees", ["Administration", "Manage Employees"]),
    "employees/form.html": ("'Edit Employee' if employee else 'Add Employee'", ["Administration", "Employees", "'Edit' if employee else 'Add'"]),
    "employees/pds.html": ("Personal Data Sheet", ["Administration", "Employees", "PDS"]),
    "departments/list.html": ("Manage Departments", ["Administration", "Departments"]),
    "departments/form.html": ("'Edit Department' if department else 'Add Department'", ["Administration", "Departments", "'Edit' if department else 'Add'"]),
    "positions/list.html": ("Positions", ["Administration", "Positions"]),
    "positions/form.html": ("'Edit Position' if position else 'Add Position'", ["Administration", "Positions", "'Edit' if position else 'Add'"]),
    "jo_cos_designations/list.html": ("JO/COS Designations", ["Administration", "JO/COS Designations"]),
    "jo_cos_designations/form.html": ("'Edit Designation' if designation else 'Add Designation'", ["Administration", "JO/COS Designations", "'Edit' if designation else 'Add'"]),
    "jo_cos_rates/list.html": ("JO and COS Rates", ["Administration", "JO and COS Rates"]),
    "jo_cos_rates/form.html": ("'Edit Rate' if rate else 'Add Rate'", ["Administration", "JO and COS Rates", "'Edit' if rate else 'Add'"]),
    "flexi_time_schedule/list.html": ("Flexi-time Schedule", ["Administration", "Flexi-time Schedule"]),
    "flexi_time_schedule/form.html": ("'Edit Schedule' if schedule else 'Add Schedule'", ["Administration", "Flexi-time Schedule", "'Edit' if schedule else 'Add'"]),
    "salary_grades/list.html": ("Salary Grades", ["Administration", "Salary Grades"]),
    "status_of_appointment/list.html": ("Status of Appointment", ["Administration", "Status of Appointment"]),
    "leave_settings/list.html": ("Leave Settings", ["Leave Management", "Leave Settings"]),
    "dtr_records.html": ("DTR Records", ["Time & Attendance", "DTR Records"]),
    "dtr_upload.html": ("Upload DTR Log", ["Time & Attendance", "Upload DTR Log"]),
    "dtr_justifications/list.html": ("DTR Justifications", ["Time & Attendance", "DTR Justifications"]),
    "dtr_flexible_worktime.html": ("Flexible Worktime", ["Time & Attendance", "Flexible Worktime"]),
    "dtr_regenerate.html": ("Regenerate DTR", ["Time & Attendance", "Regenerate DTR"]),
    "dtr_regenerate_summary.html": ("Regenerate DTR Summary", ["Time & Attendance", "Regenerate DTR", "Summary"]),
    "dtr_overtime_authorization.html": ("Overtime Authorization", ["Time & Attendance", "Overtime Authorization"]),
    "dtr_jo_cos_extend_service.html": ("Extend JO/COS Service", ["Time & Attendance", "Extend JO/COS Service"]),
    "leave_credits/list.html": ("Leave Credits", ["Leave Management", "Leave Credits"]),
    "leave_credits/card.html": ("Leave Credit Card", ["Leave Management", "Leave Credit Card"]),
    "leave_online/apply.html": ("Apply for Leave", ["Leave Management", "Apply for Leave"]),
    "leave_online/list.html": ("My Leave Requests", ["Leave Management", "My Leave Requests"]),
    "leave_online/detail.html": ("Leave Request", ["Leave Management", "Leave Request"]),
    "leave_online/approvals.html": ("Leave Approvals", ["Leave Management", "Leave Approvals"]),
    "overtime_credits/view.html": ("Overtime Credits", ["Time & Attendance", "Overtime Credits"]),
    "overtime_credits/approvals.html": ("Overtime Approvals", ["Time & Attendance", "Overtime Approvals"]),
    "payroll/gsis.html": ("GSIS", ["Payroll", "Deductions", "GSIS"]),
    "payroll/hdmf.html": ("HDMF", ["Payroll", "Deductions", "HDMF"]),
    "payroll/phic.html": ("PhilHealth", ["Payroll", "Deductions", "PhilHealth"]),
    "payroll/lbp.html": ("LBP", ["Payroll", "Deductions", "LBP"]),
    "payroll/jofemco.html": ("JOFEMCO", ["Payroll", "Deductions", "JOFEMCO"]),
    "payroll/withholding_tax.html": ("Withholding Tax", ["Payroll", "Deductions", "Withholding Tax"]),
    "payroll/worktime_summary.html": ("Worktime Summary", ["Payroll", "Worktime Summary"]),
    "payroll/jo_cos.html": ("JO/COS Payroll", ["Payroll", "JO/COS Payroll"]),
    "payroll/plantilla.html": ("Plantilla Payroll", ["Payroll", "Plantilla Payroll"]),
    "payroll/jo_cos_my_payslips.html": ("My Payslips", ["My Records", "My Payslips"]),
    "payroll/jo_cos_payslip.html": ("Payslip", ["My Records", "Payslip"]),
    "reports/employees_list.html": ("Employees Report", ["Reports", "Employees List"]),
    "about/users_manual.html": ("User's Manual", ["Help & Resources", "User's Manual"]),
    "about/hrmdo_manual.html": ("HRMDO Manual", ["Help & Resources", "HRMDO Manual"]),
    "notifications/list.html": ("Notifications", ["Alerts", "Notifications"]),
}


def rel(path: Path) -> str:
    return path.relative_to(TEMPLATES).as_posix()


def make_nav_block(nav_title: str, crumbs: list[str]) -> str:
    if " if " in nav_title or nav_title.startswith("'"):
        title_line = f"{{% set navbar_title = {nav_title} %}}"
    else:
        title_line = f"{{% set navbar_title = '{nav_title}' %}}"
    lines = [title_line]
    lines.append("{% set breadcrumbs = [")
    lines.append("    {'label': 'Home', 'url': url_for('routes.index')},")
    for i, label in enumerate(crumbs):
        is_last = i == len(crumbs) - 1
        comma = "" if is_last else ","
        if " if " in label or label.startswith("'"):
            lines.append(f"    {{'label': {label}}}{comma}")
        else:
            lines.append(f"    {{'label': '{label}'}}{comma}")
    lines.append("] %}")
    return "\n".join(lines)


def strip_legacy_shell(text: str, *, pds: bool = False) -> str:
    text = text.replace('{% extends "base.html" %}', '{% extends "layouts/app.html" %}')
    text = text.replace("{% extends 'base.html' %}", "{% extends 'layouts/app.html' %}")
    text = re.sub(r"{% block content %}", "{% block app_content %}", text, count=1)

    if pds:
        # Remove header-bar block inside pds-container
        text = re.sub(
            r'<div class="header-bar">.*?(?=<div class="pds-content">)',
            "",
            text,
            count=1,
            flags=re.DOTALL,
        )
        return text

    # Remove dashboard-container + optional header-bar through dashboard-content opening
    pattern = re.compile(
        r"(\{% block app_content %\}\s*)"
        r"(?:<div class=\"dashboard-container\">\s*)?"
        r"(?:<div class=\"header-bar\">.*?</div>\s*)?"
        r"<div class=\"dashboard-content\">\s*",
        re.DOTALL,
    )
    if not pattern.search(text):
        # bare dashboard-container without header (dtr_justifications)
        pattern2 = re.compile(
            r"(\{% block app_content %\}\s*)<div class=\"dashboard-container\">\s*<div class=\"dashboard-content\">\s*",
            re.DOTALL,
        )
        text = pattern2.sub(r"\1", text, count=1)
    else:
        text = pattern.sub(r"\1", text, count=1)

    # Remove closing dashboard-content + dashboard-container before final endblock of app_content
    text = re.sub(
        r"(\n\s*)</div>\s*</div>\s*(\{% endblock %\})\s*$",
        r"\n\2\n",
        text,
        count=1,
    )
    return text


def update_print_css(text: str) -> str:
    text = text.replace(".header-bar, .print-actions", ".app-topbar, .app-sidebar, .print-actions")
    text = text.replace(".header-bar { display: none", ".app-topbar, .app-sidebar { display: none")
    text = text.replace(".header-bar, .print-actions { display:none", ".app-topbar, .app-sidebar, .print-actions { display:none")
    text = text.replace(".dashboard-container { background", ".app-main-content { background")
    return text


def migrate_file(path: Path) -> bool:
    rel_path = rel(path)
    if rel_path in SKIP or rel_path.startswith("includes/"):
        return False

    text = path.read_text(encoding="utf-8")
    if 'extends "layouts/app.html"' in text or "extends 'layouts/app.html'" in text:
        return False
    if 'extends "base.html"' not in text and "extends 'base.html'" not in text:
        return False

    pds = rel_path == "employees/pds.html"
    text = strip_legacy_shell(text, pds=pds)
    text = update_print_css(text)

    meta = PAGE_META.get(rel_path)
    if meta:
        nav_title, crumbs = meta
        nav_block = make_nav_block(nav_title, crumbs)
        # Insert after title block
        m = re.search(r"(\{% block title %\}.*?\{% endblock %\})", text, re.DOTALL)
        if m:
            insert_at = m.end()
            if "navbar_title" not in text:
                text = text[:insert_at] + "\n\n" + nav_block + text[insert_at:]
        else:
            if "navbar_title" not in text:
                text = nav_block + "\n\n" + text

    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    migrated = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        if migrate_file(path):
            migrated.append(rel(path))
    print(f"Migrated {len(migrated)} templates:")
    for name in migrated:
        print(f"  - {name}")


if __name__ == "__main__":
    main()

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "app" / "templates"


def fix_employees_list() -> None:
    p = ROOT / "employees" / "list.html"
    text = p.read_text(encoding="utf-8")
    text = re.sub(
        r"(\{% set can_manage_employees[^\n]+\n)"
        r"\s+\{% with messages = get_flashed_messages\(with_categories=true\) %\}"
        r".*?"
        r'<div class="dashboard-content">\s+'
        r"\{% with messages = get_flashed_messages\(with_categories=true\) %\}",
        r"\1{% with messages = get_flashed_messages(with_categories=true) %}",
        text,
        count=1,
        flags=re.DOTALL,
    )
    p.write_text(text, encoding="utf-8")


def fix_payslip() -> None:
    p = ROOT / "payroll" / "jo_cos_payslip.html"
    text = p.read_text(encoding="utf-8")
    pat = re.compile(
        r'<div class="dashboard-container">\s*.*?<div class="dashboard-content[^"]*">\s*',
        re.DOTALL,
    )
    text = pat.sub("", text, count=1)
    text = text.replace('class="dashboard-content payslip-page"', 'class="payslip-page"')
    p.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    fix_employees_list()
    fix_payslip()
    print("fixed")

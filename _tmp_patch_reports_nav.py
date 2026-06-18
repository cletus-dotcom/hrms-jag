import pathlib

root = pathlib.Path(r"c:\python\hrms\app\templates")
old = """                        <a href="{{ url_for('routes.payroll_summary') }}" class="dropdown-item">Payroll Summary Report</a>
                        <a href="{{ url_for('routes.payroll_submissions') }}" class="dropdown-item">Submitted Payroll History</a>
"""
new = """                        {% include 'includes/reports_dropdown_links.html' %}"""
old2 = """                            <a href="{{ url_for('routes.payroll_summary') }}" class="dropdown-item">Payroll Summary Report</a>
                            <a href="{{ url_for('routes.payroll_submissions') }}" class="dropdown-item">Submitted Payroll History</a>
"""
new2 = """                            {% include 'includes/reports_dropdown_links.html' %}"""
for p in root.rglob("*.html"):
    t = p.read_text(encoding="utf-8")
    ot = t
    if old in t:
        t = t.replace(old, new)
    if old2 in t:
        t = t.replace(old2, new2)
    if t != ot:
        p.write_text(t, encoding="utf-8")
        print("OK", p.relative_to(root))

"""Build HRMS User's Manual as Word (.docx) from the in-app guide content."""
from __future__ import annotations

import os
import re
import shutil
from datetime import date

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT_STATIC = os.path.join(ROOT, 'app', 'static', 'docs', 'HRMS_Users_Manual.docx')
OUT_DATA = os.path.join(ROOT, 'data', 'HRMS_Users_Manual.docx')

NAVY = RGBColor(0x01, 0x3A, 0x63)
MUTED = RGBColor(0x47, 0x55, 0x69)
CODE_BG_NOTE = RGBColor(0x1F, 0x4A, 0xA5)

_MARK = re.compile(r'(\*\*[^*]+\*\*|`[^`]+`)')


def _set_run_font(run, name='Calibri', size=11, bold=False, color=None):
    run.font.name = name
    run._element.rPr.rFonts.set(qn('w:eastAsia'), name)
    run.font.size = Pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color


def add_runs(p, text, size=11):
    for part in _MARK.split(text or ''):
        if not part:
            continue
        if part.startswith('**') and part.endswith('**'):
            run = p.add_run(part[2:-2])
            _set_run_font(run, size=size, bold=True, color=NAVY)
        elif part.startswith('`') and part.endswith('`'):
            run = p.add_run(part[1:-1])
            _set_run_font(run, name='Consolas', size=max(9, size - 1), color=CODE_BG_NOTE)
        else:
            run = p.add_run(part)
            _set_run_font(run, size=size)


def heading(doc, text, level=1):
    p = doc.add_heading(text, level=level)
    for run in p.runs:
        run.font.color.rgb = NAVY
        run.font.name = 'Calibri'
    return p


def para(doc, text, *, muted=False, space_after=8):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.15
    add_runs(p, text, size=11)
    if muted and p.runs:
        for r in p.runs:
            if r.font.color.rgb != CODE_BG_NOTE:
                r.font.color.rgb = MUTED
    return p


def bullets(doc, items, numbered=False):
    style = 'List Number' if numbered else 'List Bullet'
    for item in items:
        p = doc.add_paragraph(style=style)
        p.paragraph_format.space_after = Pt(3)
        p.clear()
        add_runs(p, item, size=11)


def setup_styles(doc):
    styles = doc.styles
    normal = styles['Normal']
    normal.font.name = 'Calibri'
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(8)
    for name, size in (('Heading 1', 18), ('Heading 2', 14), ('Heading 3', 12)):
        st = styles[name]
        st.font.name = 'Calibri'
        st.font.size = Pt(size)
        st.font.color.rgb = NAVY
        st.font.bold = True


def build() -> str:
    doc = Document()
    setup_styles(doc)
    for section in doc.sections:
        section.top_margin = Inches(0.85)
        section.bottom_margin = Inches(0.85)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run('Municipality of Jagna · Human Resource Management System')
    _set_run_font(r, size=11, color=MUTED)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = title.add_run("HRMS User's Manual")
    _set_run_font(r, size=22, bold=True, color=NAVY)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub.add_run(
        'Leave, DTR, event attendance, payroll, eRMT, Service Record / Leave Credits, '
        'Employee ID Cards, and self-service payslips.'
    )
    _set_run_font(r, size=11, color=MUTED)

    dated = doc.add_paragraph()
    dated.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = dated.add_run(f'Updated {date.today().strftime("%d %B %Y")}')
    _set_run_font(r, size=10, color=MUTED)

    para(
        doc,
        'This Word file matches the in-app guide under **Help & Resources → User\'s Manual**. '
        'Menus differ by role (Admin/HR, Manager, Employee, DTR Uploader, Payroll Maker). '
        'Use the role section that matches your account.',
    )

    heading(doc, 'Contents', 1)
    bullets(
        doc,
        [
            'Quick start',
            'Navigation and menus',
            'DTR-to-payroll workflow',
            'Guides by role (Admin & HR, Manager, Employee, DTR Uploader, Payroll Maker)',
            'Common tasks (step-by-step)',
            'Event attendance',
            'eRMT (electronic records)',
            'Employee ID Cards and ID photo',
            'Best practices',
            'Troubleshooting',
        ],
        numbered=True,
    )

    heading(doc, '1. Quick start', 1)
    bullets(
        doc,
        [
            '**Log in** using your assigned Employee ID and password.',
            '**Check your dashboard** for shortcuts and status counts.',
            '**Do your task** (apply leave, upload DTR, manage employees, etc.).',
            '**Log out** when you are done—especially on shared computers.',
        ],
        numbered=True,
    )

    heading(doc, '2. Navigation and menus', 1)
    para(
        doc,
        'Your menu may look slightly different depending on your role (Admin/HR, Manager, Employee, DTR Uploader, Payroll Maker).',
        muted=True,
    )
    bullets(
        doc,
        [
            '**Dashboard** — Home screen with quick stats and recent activity.',
            '**eRMT** — Search electronic records by type, keywords, and cabinet/shelf. PDFs open from the office NAS path (not uploaded to the app server).',
            '**Settings (Admin/HR)** — Users, employees, departments, positions, JO/COS designations and rates, flexi-time, salary grades, status of appointment, leave settings, and (portal admin) Portal Console.',
            '**Daily Time Records (DTR)** — My DTR / View DTR, upload logs, justifications, flexible worktime, overnight punch fix, and (HR) quincena processing, monthly submissions, prepare period, process worktime, overtime and X-Service authorization.',
            '**Leave Management** — Apply/view leave; employees may cancel their own applications; Department Heads, Admin, and HR approve or disapprove (including when the applicant is a department head). CSC Form 6 is printable after approval or disapproval. Admin/HR may edit or cancel an application and maintain leave credits / workweek calendar.',
            '**Attendance (Admin/HR)** — `Time & Attendance → Attendance`: upload Excel check-ins (Flag Raising, Flag Retreat, Meeting, or a typed custom category) and filter the list by category.',
            '**Payroll** — Deductions (GSIS, HDMF, PHIC, LBP, JOFEMCO, withholding tax) for Admin/HR only. Worktime Summary, JO/COS (ATM / NON-ATM funds release), and Plantilla payroll for Admin/HR and Payroll Maker (Payroll Maker is limited to the department linked to their account).',
            '**Reports** — Employees List (Admin/HR/Payroll Maker). Service Records, Leave Credits, and Employee ID Cards (Admin/HR).',
            '**My Records** — When your account is linked to an employee: My PDS, My Service Record, My Payslips (and overtime credits for JO/COS).',
        ],
    )

    heading(doc, '3. DTR-to-payroll workflow', 1)
    para(
        doc,
        'Time and attendance moves through clear phases each quincena. HR tracks status on `Time & Attendance → Quincena Processing`.',
    )
    bullets(
        doc,
        [
            '**Prepare DTR period** (HR/Admin) — Generate blank rows for **all employees** in the quincena, plus holidays and the year workweek calendar (or fallback segments). Set flexible worktime, overtime authorization, and X-Service authorization.',
            '**Upload DTR log** (HR/Admin/DTR Uploader) — Upload the biometric `.dat` file for the quincena. Employees are notified to review their DTR.',
            '**Employee review** — Employees open `My DTR`, check late/undertime/absence flags, submit justifications, and may print their DTR.',
            '**HR review & adjustment** — HR closes employee review, approves or rejects justifications, and uses `Adjust DTR` to correct times or remarks.',
            '**Process worktime** (HR/Admin) — Recomputes late, undertime, absences, worktime summaries, leave ledger entries, and JO/COS overtime credits.',
            '**Payroll** (HR/Admin; Payroll Maker for their department) — Worktime Summary, JO/COS or Plantilla payroll, and payslips. Deduction uploads stay Admin/HR. JO/COS wages can be marked ATM or NON-ATM per employee.',
        ],
        numbered=True,
    )
    para(
        doc,
        '**Quincena statuses:** Draft → Review open → Review closed → Processed. After a quincena is **Processed**, DTR rows are locked until HR reopens the period from Quincena Processing.',
        muted=True,
    )

    heading(doc, '4. Guides by role', 1)

    heading(doc, 'Admin & HR', 2)
    para(
        doc,
        '**Sidebar access:** eRMT (add/edit; Admin can delete), Settings, full Time & Attendance (including Attendance upload/view and Processing), Leave Credits, Workweek calendar, Payroll (deductions + processing), Reports (Employees List, Service Records, Leave Credits, Employee ID Cards), Notifications.',
    )
    para(doc, '**Typical quincena duties:**')
    bullets(
        doc,
        [
            'Open `Quincena Processing` and confirm setup checklist (prepare period, authorizations).',
            'Use `Prepare DTR Period` to create blank rows, apply the workweek calendar for the quincena, and mark holidays.',
            'Upload DTR log or verify uploader completed upload.',
            'Monitor pending justifications; approve or reject with supporting review.',
            'Track plantilla `Monthly DTR Submissions` after the 2nd quincena upload.',
            'Close employee review when the deadline passes.',
            'Use `View DTR Records → Adjust DTR` to correct employee rows.',
            'Run `Process Worktime`, then review `Worktime Summary` and proceed to JO/COS or Plantilla payroll (ATM / NON-ATM filter on JO/COS).',
        ],
        numbered=True,
    )
    para(doc, '**Also:**')
    bullets(
        doc,
        [
            '**Employees:** Add/edit employees and PDS; for Job Order / COS set designation and **JO/COS funds release** (ATM or NON-ATM); link **ID Photo (eRMT)**; open **Service Record** or **Print ID** from the list/form.',
            '**Attendance:** `Time & Attendance → View / Upload Attendance`. Upload Excel (`employeeId`, `timestamp`, `lat`, `long`) with event date; pick Flag Raising / Flag Retreat / Meeting or type a new category. Filter the list by category (presets plus any custom names already uploaded).',
            '**Leave applications:** Approve or disapprove pending leave from the dashboard or `Leave → Leave Approvals` (in addition to the Department Head, including when the applicant is a department head). Employees may cancel their own pending or approved leave. HR/Admin may edit dates/reason/type or cancel an application (approved cancellations reverse the leave-ledger deduction). After approval or disapproval, HR and the employee can print CSC Form 6.',
            '**Leave credits:** Maintain ledger under `Leave → Leave Credits`; export Leave Credits PDF from the card or `Reports → Leave Credits`. Set compressed vs regular months and Friday-holiday weeks under `Leave → Workweek calendar` (flagged weeks charge 1.000 VL/SL/LWOP and remark `5-day workweek`).',
            '**Reports:** `Reports → Service Records`, `Leave Credits`, and `Employee ID Cards`; Employees List export (CSV/Excel/PDF).',
            '**Payroll:** Upload/manage deductions, load JO/COS and Plantilla payroll, generate payslips for employee self-service. On a JO/COS employee form (or inline on `Payroll → JO/COS`), set **Funds release** to ATM or NON-ATM; filter the payroll list to print those batches separately.',
            '**eRMT:** Encode records with cabinet/shelf and **Network PDF path (NAS)** (including type **ID Photo**).',
            '**Admin only:** Delete eRMT records (DB entry only; NAS file is kept). Portal Console when signed in as portal admin.',
        ],
    )

    heading(doc, 'Manager', 2)
    para(
        doc,
        '**Sidebar access:** Dashboard, eRMT (view/search), DTR records (department scope), leave apply/view, leave approvals, overtime offset approvals, My Records (if linked), Notifications.',
    )
    bullets(
        doc,
        [
            '**Leave:** Approve or reject leave for staff in your department via `Leave → Leave Approvals`. Office heads listed as department manager can act even if their user role is Employee. HR/Admin can also approve or disapprove the same applications (including a department head’s own leave).',
            '**DTR:** View DTR for employees in your managed department(s). You cannot upload logs or process worktime.',
            '**Overtime offset:** Review JO/COS overtime offset requests when assigned.',
            '**Self-service:** Apply leave, review your own DTR/justifications if linked, view My PDS / Service Record / Payslips.',
            '**eRMT:** Search and open NAS PDF links; you cannot add or edit records.',
        ],
    )

    heading(doc, 'Employee', 2)
    para(
        doc,
        '**Sidebar access:** Dashboard, eRMT (view/search), `My DTR`, Leave self-service, My PDS, My Service Record, My Payslips, Notifications.',
    )
    para(doc, '**When DTR is uploaded for your quincena:**')
    bullets(
        doc,
        [
            'Open `Time & Attendance → My DTR` (you receive a notification).',
            'Review each day for **Late**, **Undertime**, or **Absent** flags.',
            'Click **Justify this day** on a flagged row before the review deadline; attach proof when available.',
            'Use **Print DTR** to download a PDF copy (CSC Form 48). Plantilla prints a **Remarks** column with the justification reason when one exists; JO/COS prints Remarks / Accomplishment.',
            'HR may still adjust your record before worktime is processed; check Notifications for updates.',
        ],
        numbered=True,
    )
    para(
        doc,
        '**Plantilla — monthly submission:** After the 2nd quincena upload, open `My DTR → Month (monthly submission)`, justify remaining flagged days, **Print monthly DTR**, then **Submit monthly DTR** before the deadline (default: 5th of the following month).',
    )
    bullets(
        doc,
        [
            '**Leave:** `Leave → Apply for Leave`; track under `View Leave`. You may **cancel** a pending or approved application (approved cancellations reverse the leave-credit deduction). Print CSC Form 6 after approval or disapproval.',
            '**My Records:** My PDS, My Service Record (appointment history), My Payslips (JO/COS or Plantilla). Dashboard shows your ID photo when HR has linked one.',
            '**JO/COS:** Overtime Credits when applicable.',
            '**eRMT:** Search records; use **Copy path** or try browser open for NAS PDFs (open via File Explorer / Adobe if the browser blocks `file://`).',
        ],
    )

    heading(doc, 'DTR Uploader', 2)
    para(
        doc,
        '**Sidebar access:** Dashboard, eRMT (view), View DTR Records, Upload DTR Log, Leave self-service, My Records (if linked), Notifications.',
    )
    para(doc, '**Primary task:** Upload biometric attendance files for the correct quincena.')
    bullets(
        doc,
        [
            'Confirm with HR which quincena and file to upload.',
            'Open `Time & Attendance → Upload DTR Log`.',
            'Select the quincena, choose the `.dat` file, and click **Upload and Save**.',
            'Review the summary (records saved, dates outside quincena skipped, missing days).',
            'If you uploaded the wrong file, use **Undo upload** on your own recent batch (Admin/HR can undo any batch).',
        ],
        numbered=True,
    )
    para(
        doc,
        'You can view DTR records and use self-service leave/DTR if your account is linked to an employee. You cannot process worktime, upload event attendance, or run payroll.',
        muted=True,
    )

    heading(doc, 'Payroll Maker', 2)
    para(
        doc,
        '**Sidebar access:** Dashboard, eRMT (view), View DTR Records, DTR Justifications, Leave self-service, `Payroll` (Worktime Summary, JO/COS, Plantilla — your department), Reports → Employees List, My Records (if linked), Notifications.',
    )
    bullets(
        doc,
        [
            '**DTR records:** View attendance for active employees in your department.',
            '**Justifications:** Review submissions; use **Forward** to send to HR/Admin for approve/reject.',
            '**Payroll:** Load Worktime Summary, JO/COS, and Plantilla for your department; set JO/COS ATM or NON-ATM; generate payslips and export PDFs. Deduction uploads remain Admin/HR.',
            '**Reports:** `Reports → Employees List` (CSV, Excel, or PDF). Service Records, Leave Credits, and Employee ID Cards are Admin/HR only.',
            '**eRMT:** View/search only.',
        ],
    )
    para(
        doc,
        'Process Worktime (quincena close) and deduction uploads remain HR/Admin. Your payroll screens are limited to the department linked to your employee record. If you see “No department is linked,” ask HR to link your user to an employee in that office.',
        muted=True,
    )

    heading(doc, '5. Common tasks (step-by-step)', 1)

    heading(doc, 'A) Change your password', 2)
    bullets(
        doc,
        [
            'Click your name at the top-right.',
            'Select **Change password**.',
            'Enter your current password and your new password.',
            'Click **Change password**.',
        ],
        numbered=True,
    )

    heading(doc, 'B) Add a new employee (Admin/HR)', 2)
    bullets(
        doc,
        [
            'Go to `Settings → Manage Employees`.',
            'Click **+ Add Employee**.',
            'The **Employee ID** is automatically generated. Fill in the rest of the form.',
            'For Job Order / Contract of Service, choose the JO/COS designation and **JO/COS funds release** (ATM or NON-ATM). Payroll Maker can also set funds release later on `Payroll → JO/COS`.',
            'Click **Add Employee**.',
        ],
        numbered=True,
    )
    para(doc, 'Note: If you see a duplicate/invalid ID, refresh the page and try again.', muted=True)

    heading(doc, 'C) Open and update an employee PDS', 2)
    bullets(
        doc,
        [
            'Open `Employees` list.',
            'Click an employee row to open the PDS.',
            'Update fields as needed and save.',
        ],
        numbered=True,
    )

    heading(doc, 'D) Apply for leave', 2)
    bullets(
        doc,
        [
            'Open `Leave Management → Apply for Leave`.',
            'Select the leave type and dates.',
            'Add details (reason, where required).',
            'Submit your application.',
            'Check status in `Leave Management → View Leave`. You may **cancel** a pending or approved application from that list or the detail page.',
        ],
        numbered=True,
    )

    heading(doc, 'E) Approve, edit, or cancel leave', 2)
    bullets(
        doc,
        [
            'Department Head: open `Leave Management → Leave Approvals` for staff in your office (your user role may be Manager or Employee if you are listed as the department’s manager).',
            'Admin/HR: the same page and the dashboard show pending applications, including department-head applicants. You may **Approve** or **Disapprove**.',
            'Admin/HR may **edit** dates, reason, or leave type, or **cancel** an application. Cancelling an approved leave reverses the leave-ledger deduction.',
            'After approval or disapproval, HR and the employee can print CSC Form 6 (including disapproved applications).',
        ],
        numbered=True,
    )

    heading(doc, 'F) Prepare DTR period (Admin/HR)', 2)
    para(
        doc,
        'Run this **before** uploading the biometric log. It creates blank DTR rows for the selected period and can stamp rest days, holidays, and work suspensions. It does **not** fill 8:00/5:00 or 7:00/6:00 present-day times — plantilla and JO/COS keep actual biometric punches only.',
    )
    bullets(
        doc,
        [
            'Open `Time & Attendance → Prepare DTR Period` (also available from `Quincena Processing`).',
            'Select **Quincena** (1st or 2nd half), **Month**, and **Year**. Preparation always covers **all active employees** for that quincena (plantilla and non-plantilla).',
            '**Workweek calendar** — if the year is already saved under `Leave → Workweek calendar`, this modal shows that quincena’s weeks (compressed vs regular, Friday-holiday flags). You do not add workweek segments. On a compressed (4-day) week the rest day defaults to **Friday** (`NON-WORKING DAY`). If a local declaration transfers that rest day, choose another weekday (Mon–Thu) for that week. Flagged Friday-holiday weeks stay regular (5-day) and have no rest-day transfer.',
            '**Compressed workweek segments (fallback only)** — shown only when this month is *not* on the year calendar. Save the year calendar when you can; otherwise add segments for 4-day / 5-day date ranges as before.',
            '**5-day / regular** — late/undertime targets (8:00 AM / 12:00 PM / 1:00 PM / 5:00 PM). DTR In/Out stay blank until biometric upload.',
            '**4-day / compressed** — late/undertime targets on the four duty days (7:00 AM / 12:00 PM / 1:00 PM / 6:00 PM); the rest day (Friday unless transferred) is marked `NON-WORKING DAY`.',
            'Employees on the **Flexible Worktime** list (or the employee flexi flag) skip the office rest-day overlay; that day’s flexi row (including Rest mode) owns the schedule.',
            '**Holidays / work suspension** — pick dates on the calendar (full day, AM, or PM) or type one date per line (`YYYY-MM-DD`, optional `AM` or `PM`). These apply org-wide for the listed days.',
            'Before generating, set `Flexible Worktime`, `Overtime Authorization`, and `X-Service Authorization` on the same Time & Attendance menu when required for the period.',
            'Click **Generate**. Blank DTR rows are created for the quincena; compressed rest days are stamped `NON-WORKING DAY` from the year calendar (or from fallback segments if that month is unsaved).',
        ],
        numbered=True,
    )
    para(
        doc,
        'To print a **full-month** DTR for plantilla employees (monthly submission requirement), open `View DTR Records`, set **View by** to **Month**, and use **Print monthly DTR**. The PDF uses the same Civil Service Form No. 48 layout as JO/COS DTR; the last column is **Remarks** (justification reason, if any). Day-to-day payroll and review still use the **Quincena** view.',
        muted=True,
    )

    heading(doc, 'G2) Monthly plantilla DTR submissions (Admin/HR)', 2)
    bullets(
        doc,
        [
            'After the **2nd quincena** DTR is uploaded and both quincenas are closed for employee review, open `Time & Attendance → Monthly DTR Submissions`.',
            'Select month, year, and optional department filter.',
            'Review employees who have **Submitted** monthly DTR; open their month view if needed.',
            '**Accept** complete packets or **Return for resubmit** with review notes.',
            'Default employee deadline is the **5th of the following month** (plantilla employees are notified after Q2 upload and reminded near the deadline).',
        ],
        numbered=True,
    )

    heading(doc, 'G) Upload DTR log (DTR Uploader, HR, or Admin)', 2)
    bullets(
        doc,
        [
            'Open `Time & Attendance → Upload DTR Log`.',
            'Select the correct **quincena**.',
            'Choose the biometric `.dat` file and click **Upload and Save**.',
            'Read the summary: records saved, outside-quincena dates skipped, and any missing calendar days.',
            'Employees are notified to review `My DTR`. HR monitors progress on `Quincena Processing`.',
        ],
        numbered=True,
    )

    heading(doc, 'H) Review and justify your DTR (Employee)', 2)
    bullets(
        doc,
        [
            'Open `Time & Attendance → My DTR` for the quincena.',
            'On a flagged day, click **Justify this day** (or open `DTR Justifications`).',
            'Enter a clear reason and attach supporting document if available.',
            'Submit before the review deadline shown on the page.',
            'Optionally click **Print DTR** to save a PDF copy.',
        ],
        numbered=True,
    )

    heading(doc, 'I) Process a quincena (Admin/HR)', 2)
    bullets(
        doc,
        [
            'Open `Time & Attendance → Quincena Processing` and select the quincena.',
            'Complete the setup checklist (prepare period, flexi, authorizations, upload).',
            'When employees finish reviewing, click **Close employee review**.',
            'Approve/reject justifications and `Adjust DTR` as needed.',
            'Run `Process Worktime`, then open `Worktime Summary` before payroll.',
        ],
        numbered=True,
    )

    heading(doc, 'J) View worktime summary (Admin/HR; Payroll Maker for their department)', 2)
    bullets(
        doc,
        [
            'Open `Payroll → Worktime Summary`.',
            'Select year, month, quincena, employee type (Plantilla or JO/COS), and optional department or employee filters. Payroll Maker is locked to their linked department.',
            'Click **Apply** to load the report.',
            'Use **CSV** or **PDF** export if needed.',
        ],
        numbered=True,
    )

    heading(doc, 'K) Export Service Record PDF (Admin/HR)', 2)
    bullets(
        doc,
        [
            'Open `Reports → Service Records` (or an employee’s form / list action **Service Record**).',
            'Select the employee and open the Service Record page.',
            'Click **Export PDF** for a printable appointment history (municipal header, no department/position columns in the table).',
        ],
        numbered=True,
    )
    para(
        doc,
        'Employees with a linked account can view their own history under `My Records → My Service Record` (on-screen; PDF export is for Admin/HR).',
        muted=True,
    )

    heading(doc, 'L) Export Leave Credits PDF (Admin/HR)', 2)
    bullets(
        doc,
        [
            'Open `Leave → Leave Credits` and open an employee card, or use `Reports → Leave Credits`.',
            'Review the ledger (VL, SL, SPL, WL, CTO, LWOP).',
            'Click **Export PDF** (landscape A4).',
        ],
        numbered=True,
    )

    heading(doc, 'L2) Workweek calendar (Admin/HR)', 2)
    para(
        doc,
        'Open `Leave → Workweek calendar`, choose a year, then set each month to compressed (4-day / 1.250) or regular (5-day / 1.000). Official Friday holidays (including a Friday New Year that sits in the last week of December) are flagged automatically; add local Friday holidays as needed. Save to recompute VL/SL/LWOP on those dates and stamp `5-day workweek` on flagged leave lines. After a month is saved, Prepare DTR shows that quincena’s calendar (not workweek segments). Transfer a compressed rest day off Friday on the Generate DTR modal when a local declaration requires it.',
    )

    heading(doc, 'M) Run Plantilla or JO/COS payroll (Admin/HR; Payroll Maker for their department)', 2)
    bullets(
        doc,
        [
            'Confirm the quincena is **Processed** and Worktime Summary looks correct.',
            'Admin/HR: upload or refresh deduction schedules (GSIS, HDMF, PHIC, LBP, JOFEMCO, withholding tax) as needed. Payroll Maker does not upload deductions.',
            'For JO/COS, set **Funds release** to ATM or NON-ATM on the employee form (Admin/HR) or on the payroll row (Admin/HR/Payroll Maker). Default is ATM.',
            'Open `Payroll → Plantilla` or `Payroll → JO/COS`, select period and department (Payroll Maker is locked to theirs). On JO/COS, also choose Funds release: All, ATM, or NON-ATM.',
            'Load payroll, then generate payslips so employees can open them under `My Payslips`. Filter first if you need a separate ATM or NON-ATM PDF.',
        ],
        numbered=True,
    )

    heading(doc, 'O) Upload and view event attendance (Admin/HR)', 2)
    bullets(
        doc,
        [
            'Open `Time & Attendance → View / Upload Attendance` (shortcuts: Flag Raising, Flag Retreat, Meeting).',
            'Click **Upload attendance**. Set the event date, then either choose a category from the list or **type a new category** (for example a special assembly).',
            'Attach the Excel file with columns `employeeId`, `timestamp` (epoch ms), `lat`, and `long`.',
            'On the same page, use the **Category** filter to view All, a preset, or any custom category already uploaded. Search by employee ID, name, or location.',
        ],
        numbered=True,
    )

    heading(doc, 'N) Print Employee ID Cards (Admin/HR)', 2)
    bullets(
        doc,
        [
            'Ensure each employee has an **ID Photo** linked (see Employee ID Cards and ID photo).',
            'Open `Reports → Employee ID Cards` (or click **Print ID** on an employee list/form row).',
            'Optional: filter by department, status, and **ID format** (All / JO/COS only / Plantilla only).',
            'Click **Print ID** on one row for a single PDF, or select several employees and click **Print selected (batch PDF)**.',
        ],
        numbered=True,
    )
    para(
        doc,
        'Template is chosen automatically from appointment status: Job Order / Contract of Service → JO/COS ID; other plantilla appointments → Plantilla ID. Department text prefers the department *description*.',
        muted=True,
    )

    heading(doc, '6. eRMT (electronic records)', 1)
    para(
        doc,
        '**Overview → eRMT** catalogs scanned HR documents. The app stores metadata and a **Network PDF path (NAS)** only—files are not uploaded to the application server.',
    )
    para(doc, '**Who can do what:**')
    bullets(
        doc,
        [
            '**All signed-in users:** Search/filter records; copy NAS path; try browser open.',
            '**Admin / HR:** Add and edit records (record number like `ERMT-YYMMDD-NN`, type, title, keywords, cabinet, shelf, remarks, NAS path).',
            '**Admin:** Delete a record from the database (the file on the NAS remains).',
        ],
    )
    para(
        doc,
        '**Record types:** Memorandum, Office Order, Reports, Certificates, Contracts, Personnel Records, 201 File, 2316 File, **ID Photo**, Others.',
    )
    heading(doc, 'Add or update a record (Admin/HR)', 2)
    bullets(
        doc,
        [
            'Open `Overview → eRMT` and click **+ New Record** (or **Update** on an existing row).',
            'Fill record date, type, title, keywords, cabinet/shelf, and remarks.',
            'Paste the **Network PDF path** (UNC on the office NAS `\\\\192.168.20.3\\hrmdo\\...`, e.g. `\\\\192.168.20.3\\hrmdo\\ID PICTURES\\PLANTILLA\\40555.jpg`, or a mapped drive path). For ID photos, use an image path (`.jpg` / `.png` / similar). Save.',
        ],
        numbered=True,
    )
    heading(doc, 'Open a NAS PDF', 2)
    bullets(
        doc,
        [
            'Expand a record and use **Copy path**, then open the file in File Explorer, Adobe Acrobat, or Edge.',
            'Optional: **Try browser open** — many browsers block `file://` links from an http(s) site; use Copy path if that fails.',
            'Your PC must be on the office network with access to `\\\\192.168.20.3\\hrmdo`.',
        ],
        numbered=True,
    )

    heading(doc, '7. Employee ID Cards and ID photo', 1)
    para(
        doc,
        'Admin/HR can print official **LGU Jagna** ID cards. Photos are not uploaded into the app database—they are linked from an **eRMT** image file on the office NAS (same pattern as other eRMT records).',
    )
    heading(doc, '1) Encode the ID photo in eRMT (Admin/HR)', 2)
    bullets(
        doc,
        [
            'Copy the employee’s ID photo into the NAS folder `\\\\192.168.20.3\\hrmdo\\ID PICTURES\\PLANTILLA` or `\\\\192.168.20.3\\hrmdo\\ID PICTURES\\JO` (JPG/PNG recommended).',
            'In `Overview → eRMT`, create a record with type **ID Photo**.',
            'Set the **Network file path** to the full UNC path (e.g. `\\\\192.168.20.3\\hrmdo\\ID PICTURES\\PLANTILLA\\40555.jpg`) and save. Note the record number (e.g. `ERMT-YYMMDD-NN`).',
        ],
        numbered=True,
    )
    heading(doc, '2) Link the photo to the employee (Admin/HR)', 2)
    bullets(
        doc,
        [
            'Open `Settings → Employees` and edit the employee.',
            'In **ID Photo (eRMT)**, enter the eRMT **record number** and save.',
            'A preview appears when the path is readable from the app server. The same photo can show on the dashboard welcome card.',
        ],
        numbered=True,
    )
    heading(doc, '3) Print Plantilla or JO/COS ID cards (Admin/HR)', 2)
    bullets(
        doc,
        [
            'Open `Reports → Employee ID Cards`.',
            'Filter by department, employment status, and ID format if needed.',
            'Use **Print ID** for one employee, or select multiple rows and **Print selected (batch PDF)**.',
            'You can also print from the employee list/form via the **Print ID** action.',
        ],
        numbered=True,
    )
    para(doc, '**What appears on the card:**')
    bullets(
        doc,
        [
            '**Plantilla:** Official plantilla template; photo (~3.5 × 4.5 cm) with gold frame; name as `FIRSTNAME M.I. LASTNAME`, department description, and ID NO box on the lower left; QR code of the employee ID on the lower right.',
            '**JO/COS:** JO/COS template (auto-selected for Job Order / Contract of Service); photo in the template slot; condensed name, department description, and ID NO box on the lower left; QR code of the employee ID on the lower right.',
        ],
    )
    para(
        doc,
        'If the photo is missing, the card still prints with an empty photo area—link a valid eRMT ID Photo first for a complete card.',
        muted=True,
    )

    heading(doc, '8. Best practices', 1)
    bullets(
        doc,
        [
            '**Follow the quincena workflow**: Do not run payroll until worktime is processed for that quincena.',
            '**Close employee review** before adjusting DTR and processing worktime.',
            '**Be consistent**: Use complete names and correct departments to keep reports accurate.',
            '**Double-check dates**: Leave and DTR are date-sensitive; wrong dates cause payroll issues. Save the year on `Leave → Workweek calendar` before Prepare DTR so the quincena uses that scheme (and transfer a rest day on the Generate DTR modal only when a local declaration requires it).',
            '**JO/COS funds release**: Set ATM vs NON-ATM before generating payslips if you need separate payment batches.',
            '**Attach proof when needed**: Justifications are faster to review with supporting files.',
            '**eRMT paths**: Prefer stable UNC paths on the shared NAS; avoid personal desktop folders.',
            '**ID photos**: Use a clear front-facing photo; keep the eRMT path pointing at the current file after NAS folder moves.',
            '**ID card print**: Filter by Plantilla vs JO/COS when batch-printing so you load the correct paper/stock.',
            '**Use search**: Most lists support searching—use it instead of scrolling.',
            '**Log out**: Always log out after using the system on shared devices.',
        ],
    )

    heading(doc, '9. Troubleshooting', 1)
    heading(doc, 'Login problems', 2)
    bullets(
        doc,
        [
            '**“Invalid credentials”**: Check your Employee ID and password. Make sure Caps Lock is off.',
            '**Forgot password**: Ask Admin/HR to reset your account password.',
            '**Stuck on login**: Refresh the page. If it persists, restart the browser and try again.',
        ],
    )
    heading(doc, 'Pages not showing / missing menus', 2)
    bullets(
        doc,
        [
            'Your role may not have permission for that page. Confirm your role with Admin/HR.',
            'Try logging out and logging back in after role changes.',
            'Service Records / Leave Credits / Employee ID Cards under Reports, deduction uploads, event attendance, and eRMT edit/delete require Admin or HR (delete = Admin).',
            'Payroll Maker should see `Payroll → Worktime Summary`, JO/COS, and Plantilla for their department after a fresh login. Deduction menus stay Admin/HR.',
        ],
    )
    heading(doc, 'Prepare DTR / workweek issues', 2)
    bullets(
        doc,
        [
            '**Year calendar already saved**: Prepare DTR shows the quincena workweek, not “Add workweek segment.” Change 4-day vs 5-day on `Leave → Workweek calendar`; transfer a compressed rest day on the Generate DTR week table.',
            '**Overlapping segments** (only if the month is not on the year calendar): Two segments for the same employee group cannot cover the same dates.',
            '**Wrong rest day**: Compressed weeks default to Friday. If the rest day was transferred locally, pick the new weekday on Generate DTR before generating.',
            '**No default times stamped**: The calendar (or segments) only mark rest days. In/Out times come from biometric upload.',
        ],
    )
    heading(doc, 'DTR upload issues', 2)
    bullets(
        doc,
        [
            '**Wrong quincena**: Select the correct quincena before uploading. Dates outside the quincena are skipped automatically.',
            '**Incomplete quincena in file**: Missing calendar days are listed in the upload summary; you can upload another file or let HR adjust.',
            '**Employee ID not found**: Ensure the employee exists and the ID in the file matches the employee record.',
            '**Duplicate upload**: New uploads **merge** with existing rows for the same day. Use **Undo upload** on the batch if the entire upload was wrong.',
            '**Cannot adjust DTR**: If the quincena is **Processed**, HR must **Reopen for adjustment** on Quincena Processing first.',
        ],
    )
    heading(doc, 'DTR justification issues', 2)
    bullets(
        doc,
        [
            '**Cannot submit justification**: The employee review window may be closed or past the deadline. Contact HR.',
            '**Flag still showing after justification**: Justifications are documentation; HR must approve and may still adjust DTR times before worktime is processed.',
            '**Payroll Maker**: Use **Forward** to send justifications to HR; only HR/Admin can approve or reject.',
        ],
    )
    heading(doc, 'eRMT / NAS PDF issues', 2)
    bullets(
        doc,
        [
            '**Browser won’t open the PDF**: Copy the path and open it in File Explorer or Adobe. Browsers often block local/network `file://` links from web apps.',
            '**HRMS won’t start / hangs while the NAS is offline**: Startup no longer waits on `\\\\192.168.20.3\\hrmdo`. NAS login runs in the background; pages load without photos/PDFs until the share is back. To disable NAS attempts entirely, set `ERMT_NAS_SKIP=1` in `.env` and restart.',
            '**Cannot add/edit**: Only Admin/HR can encode records. Only Admin can delete.',
        ],
    )
    heading(doc, 'ID photo / ID card issues', 2)
    bullets(
        doc,
        [
            '**No photo on card or dashboard**: Link an eRMT record (type **ID Photo**) on the employee form; confirm the NAS image path exists and the app server can read it.',
            '**Works on dev PC but not on server (`hrms.exe`)**: The server process must log in to the NAS share. Either place a `.env` file in the same folder as `hrms.exe`, or set Windows environment variables before starting HRMS: `ERMT_NAS_ROOT=\\\\192.168.20.3\\hrmdo`, `ERMT_NAS_USER=hrmdo`, `ERMT_NAS_PASSWORD=…` (share password). Restart `hrms.exe` and check the console for **NAS connected**.',
            '**Wrong template (Plantilla vs JO/COS)**: Check the employee’s appointment / employment status. JO and Contract of Service use the JO/COS template; other plantilla statuses use the Plantilla template.',
            '**Blank or incomplete name/department**: Complete the employee name fields and department description (falls back to department name).',
            '**Batch print empty**: Select at least one visible row after filtering; “Select all visible” only covers the current filtered list.',
        ],
    )
    heading(doc, 'Leave printing / export issues', 2)
    bullets(
        doc,
        [
            'CSC Form 6 print is available to the employee and to HR after the application is **Approved** or **Disapproved**.',
            'If a department head’s leave is missing from Leave Approvals, sign in as Admin/HR — they can act on any pending application, including office heads.',
            'If PDF export looks clipped, try browser print settings: set paper to A4 and margins to “Default”.',
            'Leave Credits PDF is Admin/HR only (landscape).',
        ],
    )
    heading(doc, 'Event attendance issues', 2)
    bullets(
        doc,
        [
            '**Category not in the view filter**: Type it on upload; after a successful upload it appears in the Category dropdown.',
            '**Unmatched badge**: The Excel `employeeId` must match an employee ID in HRMS (leading zeros are tolerated).',
            '**Wrong file type**: Upload `.xlsx` (not the biometric `.dat` used for DTR).',
        ],
    )
    heading(doc, 'JO/COS payroll / funds release', 2)
    bullets(
        doc,
        [
            'Default funds release is **ATM**. Change it on the JO/COS employee form or on the payroll row; then filter All / ATM / NON-ATM before export.',
            'Payroll Maker cannot open deduction pages. They can load payroll and generate payslips for their department only.',
        ],
    )
    heading(doc, 'If something looks wrong', 2)
    bullets(
        doc,
        [
            'Refresh the page first (sometimes data updates after saving).',
            'Check if you’re on the correct page/menu.',
            'If the error message mentions “database” or “constraint”, contact Admin/HR and include a screenshot of the message.',
        ],
    )

    footer = doc.add_paragraph()
    footer.paragraph_format.space_before = Pt(18)
    r = footer.add_run('LGU Jagna HRMS · User’s Manual · for internal office use')
    _set_run_font(r, size=9, color=MUTED)

    os.makedirs(os.path.dirname(OUT_STATIC), exist_ok=True)
    os.makedirs(os.path.dirname(OUT_DATA), exist_ok=True)
    doc.save(OUT_STATIC)
    shutil.copy2(OUT_STATIC, OUT_DATA)
    return OUT_STATIC


if __name__ == '__main__':
    path = build()
    print(f'Wrote {path}')
    print(f'Copied {OUT_DATA}')

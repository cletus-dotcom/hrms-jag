"""Platform about page content and developer attribution (TBGP-style)."""

PLATFORM_ABOUT = {
    "page_title": "About the Platform",
    "title": "About the HRMS Portal",
    "tagline": (
        "Human resources, timekeeping, leave, payroll, electronic records, and reports — "
        "one internal system for LGU HRMDO operations."
    ),
    "intro": (
        "This HRMS portal centralizes employee records, daily time record workflows, online leave processing, "
        "payroll and deductions, electronic records (eRMT), and printable HR reports so Admin, HR, managers, "
        "and employees work from the same live data."
    ),
    "features": [
        (
            "Landing & sign-in",
            "Public portal landing with branding/CMS; Employee Sign-In and related systems (eRMT, CBAT, HRIS).",
        ),
        (
            "Employee 201 / PDS",
            "Maintain personnel records, appointments, salary grade/step, Personal Data Sheet (PDS), "
            "and link an ID photo via an eRMT image record on the NAS.",
        ),
        (
            "Service Records",
            "View employment/appointment history; Admin/HR can export Service Record PDF per employee.",
        ),
        (
            "Employee ID Cards",
            "Print official LGU Jagna ID cards: Plantilla and JO/COS use separate templates; "
            "photo comes from the linked eRMT ID Photo; print one card or a batch PDF from Reports.",
        ),
        (
            "Daily Time Records",
            "Prepare quincena periods, upload biometric logs, justify flagged days, adjust DTR, and process worktime.",
        ),
        (
            "Leave Management",
            "Apply and approve leave online; Admin/HR maintain leave credits/ledger and export Leave Credits PDF.",
        ),
        (
            "Payroll (JO/COS)",
            "Worktime summary, JO/COS payroll load, deductions, and per-period payslip snapshots for self-service.",
        ),
        (
            "Payroll (Plantilla)",
            "Plantilla payroll by department/quincena, government shares/deductions, and payslip generation.",
        ),
        (
            "Payroll deductions",
            "GSIS, HDMF, PHIC, LBP, JOFEMCO, and withholding tax uploads/schedules for payroll runs.",
        ),
        (
            "eRMT (Electronic Records)",
            "Archive scanned/PDF/image records with cabinet/shelf location; store NAS network path links for viewing. "
            "Record type ID Photo is used for printable employee ID cards.",
        ),
        (
            "Reports",
            "Employees List (CSV/Excel/PDF), Service Records, Leave Credits, and Employee ID Cards "
            "(filter JO/COS vs Plantilla; Print ID per employee or batch PDF).",
        ),
        (
            "Notifications",
            "In-app alerts for DTR review, justifications, approvals, monthly submissions, and generated payslips.",
        ),
    ],
    "features_by_role": [
        (
            "Admin / HR",
            "Full Settings, DTR processing, leave credits, payroll, eRMT add/edit/delete (Admin delete), "
            "link employee ID photos, and all Reports including Service Record, Leave Credits, and Employee ID Cards.",
        ),
        (
            "Manager",
            "Department DTR view, leave approvals, overtime offset approvals, own leave/DTR self-service, notifications.",
        ),
        (
            "Employee",
            "My DTR / justifications, apply leave, My PDS, My Service Record, My Payslips, dashboard ID photo when linked, "
            "eRMT view (search/open NAS links).",
        ),
        (
            "DTR Uploader",
            "Upload biometric .dat logs for the selected quincena; optional undo of own upload batches.",
        ),
        (
            "Payroll Maker",
            "Department DTR and justifications (forward to HR); Reports → Employees List export.",
        ),
    ],
}


# You can replace these details to match your deployment / support contact.
PLATFORM_DEVELOPER = {
    "name": "JCG IT Solutions",
    "address": "Capt. Goyo St., Poblacion, Jagna, Bohol",
    "phone": "+63 995 225 8185",
    "phone_href": "tel:+639952258185",
    "email": "jcgfmbiz239@gmail.com",
    "email_href": "mailto:jcgfmbiz239@gmail.com",
    "blurb": (
        "JCG IT Solutions builds practical software for schools, LGUs, and community organizations in Bohol. "
        "This platform was tailored for HRMDO operations — simple to learn and steady to rely on."
    ),
}

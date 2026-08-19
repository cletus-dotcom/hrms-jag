"""Default CMS / branding content for new installations."""

DEFAULT_BRANDING = {
    'org_name': 'Human Resource Management and Development Office',
    'portal_name': 'HRMDO Portal',
    'org_location': 'Municipality of Jagna, Bohol',
    'tagline': 'Upholding Merit, Nurturing Growth, and Serving the Public with Excellence.',
    'logo_path': 'hrmdo_logo_transparent.png',
    'seal_path': 'jagna_logo.png',
    'favicon_path': '',
    'use_uploaded_logo': False,
    'use_uploaded_seal': False,
    'uploaded_logo': '',
    'uploaded_seal': '',
}

DEFAULT_LANDING_HERO = {
    'title_primary': 'Digital HR,',
    'title_secondary': 'Built for Public Service Excellence.',
    'subtitle': 'Upholding Merit, Nurturing Growth, and Serving the Public with Excellence.',
    'cta_label': 'Employee Sign-In',
    'related_links': [
        {'label': 'eRMT', 'url': '/login?next=/ermt/dashboard', 'external': False},
        {'label': 'CBAT', 'url': 'http://192.168.20.20:8040/', 'external': True},
        {'label': 'HRIS', 'url': 'https://jagna.hrisbohol.com/', 'external': True},
    ],
}

DEFAULT_LANDING_SERVICES = {
    'section_title': 'Quick Services',
    'section_lead': 'Everything you need for day-to-day HR — sign in to get started.',
    'cards': [
        {
            'icon': 'briefcase',
            'title': 'Recruitment & Careers',
            'text': 'View open positions, apply online, and track your application status under strict Civil Service standards.',
        },
        {
            'icon': 'calendar',
            'title': 'Leave & Attendance',
            'text': 'File your leaves, check your remaining balances, and view your daily time records seamlessly.',
        },
        {
            'icon': 'growth',
            'title': 'Performance & Growth',
            'text': 'Set your targets, review evaluations, and access annual training programs to level up your skills.',
        },
        {
            'icon': 'folder',
            'title': 'Personnel Records',
            'text': 'Keep your digital 201 file up to date. Secure, transparent, and always available when you need it.',
        },
    ],
}

SERVICE_ICON_OPTIONS = [
    ('briefcase', 'Briefcase (careers)'),
    ('calendar', 'Calendar (leave/attendance)'),
    ('clock', 'Clock (time records)'),
    ('folder', 'Folder (records)'),
    ('document', 'Document (forms/PDS)'),
    ('shield', 'Shield (compliance)'),
    ('growth', 'Growth (performance/training)'),
    ('users', 'Users (people)'),
]

DEFAULT_LANDING_MISSION = {
    'title': 'Committed to Accountable Public Service.',
    'text': (
        'We anchor all our personnel actions on equal opportunity and strict compliance with Civil Service laws. '
        'From your first day of onboarding to continuous capability development, we are here to support your journey in public service.'
    ),
}

DEFAULT_LANDING_STATS = {
    'section_title': 'At a Glance',
    'section_lead': 'Live aggregate figures from the HRMDO system.',
}

DEFAULT_LANDING_SPLASH = {
    'enabled': True,
    'duration_ms': 3000,
    'show_tagline': False,
    'tagline': 'Loading HRMDO Portal…',
    'bar_aria_label': 'Loading',
}

DEFAULT_LANDING_CAROUSEL = {
    'enabled': True,
    'interval_ms': 6000,
    'section_label': 'Announcements',
    'slides': [
        {
            'title': 'Welcome to the HRMDO Portal',
            'text': 'Sign in to manage leave, daily time records, and your personnel file in one place.',
            'link_url': '/login',
            'link_label': 'Sign in',
        },
        {
            'title': 'Apply for Leave Online',
            'text': 'File leave requests, track approvals, and view balances without paper forms.',
            'link_url': '/login',
            'link_label': 'Get started',
        },
        {
            'title': 'Daily Time Records',
            'text': 'Authorized staff can upload, review, and regenerate DTR for the workforce.',
            'link_url': '',
            'link_label': '',
        },
        {
            'title': 'Serving with Excellence',
            'text': 'Upholding merit, nurturing growth, and accountable public service for our LGU.',
            'link_url': '',
            'link_label': '',
        },
    ],
}

DEFAULT_LANDING_FOOTER = {
    'column1_title': 'Republic of the Philippines',
    'column1_lines': [
        'All content is in the public domain unless otherwise stated.',
        'Official Philippine government information and services may be accessed through the national portal.',
    ],
    'gov_links': [
        {'label': 'Gov.ph', 'url': 'https://www.gov.ph/'},
        {'label': 'Open Data', 'url': 'https://data.gov.ph/'},
        {'label': 'Freedom of Information', 'url': 'https://www.foi.gov.ph/'},
        {'label': 'Official Gazette', 'url': 'https://www.officialgazette.gov.ph/'},
    ],
    'column2_title': 'Transparency',
    'column2_paragraphs': [
        'This portal supports internal human resource processes of the local government unit. Published figures are aggregate system indicators and may be updated without prior notice.',
        'For concerns on data privacy, refer to the National Privacy Commission (NPC) and applicable issuances of the Department of the Interior and Local Government (DILG).',
    ],
    'seal_caption': 'Municipality of Jagna, Bohol',
    'copyright': '© 2026 All rights reserved.',
    'disclaimer': (
        'This application is operated for official LGU business. Unauthorized access or misuse is subject to administrative '
        'and criminal penalties under applicable laws. Some features require authentication; contact your HRMDO for access.'
    ),
}

CMS_KEYS = {
    'branding': DEFAULT_BRANDING,
    'landing_hero': DEFAULT_LANDING_HERO,
    'landing_services': DEFAULT_LANDING_SERVICES,
    'landing_mission': DEFAULT_LANDING_MISSION,
    'landing_stats': DEFAULT_LANDING_STATS,
    'landing_splash': DEFAULT_LANDING_SPLASH,
    'landing_carousel': DEFAULT_LANDING_CAROUSEL,
    'landing_footer': DEFAULT_LANDING_FOOTER,
}

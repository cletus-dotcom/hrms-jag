"""Restore generate_dtr_modal.html from git history of dashboard.html."""
from pathlib import Path
import subprocess

repo = Path(__file__).resolve().parents[1]
text = subprocess.check_output(
    ['git', 'show', 'fbf4b2a:app/templates/dashboard.html'],
    cwd=repo,
    text=True,
    encoding='utf-8',
)

# Styles from extra_css block (first admin/hr if block after block extra_css)
css_marker = '{% block extra_css %}\n{% if (current_user.role or \'\')|lower in (\'admin\', \'hr\') %}'
css_start = text.index(css_marker) + len('{% block extra_css %}\n')
css_end = text.index('</style>', css_start) + len('</style>')
styles_block = text[css_start:css_end]

# Modal + status overlay (second admin/hr if block before endblock content)
modal_marker = '{% if (current_user.role or \'\')|lower in (\'admin\', \'hr\') %}\n<div id="generateDtrModal"'
modal_start = text.index(modal_marker)
status_end = text.index('</div>\n{% endif %}\n{% endblock %}', modal_start) + len('</div>')
modal_block = text[modal_start:status_end]

out = repo / 'app' / 'templates' / 'includes' / 'generate_dtr_modal.html'
content = styles_block + '\n' + modal_block + '\n{% endif %}\n'
# Fix mojibake from PowerShell redirect if any slipped in
content = (
    content.replace('ΓåÆ', '→')
    .replace('┬╖', '·')
    .replace('ΓÇö', '—')
)
out.write_text(content, encoding='utf-8')
print(f'Wrote {len(content)} chars to {out}')

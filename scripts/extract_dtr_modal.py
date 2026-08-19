from pathlib import Path

p = Path(r"c:\python\hrms\app\templates\dashboard.html")
text = p.read_text(encoding="utf-8")
marker = "{% if (current_user.role or '')|lower in ('admin', 'hr') %}"
start = text.index(marker)
end = text.index("{% endif %}", start) + len("{% endif %}")
modal = text[start:end]
Path(r"c:\python\hrms\app\templates\includes\generate_dtr_modal.html").write_text(modal, encoding="utf-8")

js_start = text.index("{% block extra_js %}") + len("{% block extra_js %}")
js_end = text.rindex("{% endblock %}")
js = text[js_start:js_end].strip()
js = js.replace("<script>", "").replace("</script>", "").strip()
Path(r"c:\python\hrms\app\static\js\generate-dtr-modal.js").write_text(js + "\n", encoding="utf-8")
print("done", len(modal), len(js))

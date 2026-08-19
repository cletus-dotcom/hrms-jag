"""Site branding, CMS persistence, and uploaded media."""

from __future__ import annotations

import copy
import os
import sys
from datetime import datetime

from flask import current_app, url_for
from werkzeug.utils import secure_filename

from app import db
from app.models import SiteSetting
from app.portal_config import ALLOWED_LOGO_EXTENSIONS, SITE_UPLOAD_SUBDIR
from app.portal_defaults import CMS_KEYS


def app_persistent_data_dir() -> str:
    """
    Writable ``data/`` root that survives restarts.

    Dev: ``<project>/data``.
    PyInstaller (onefile/onedir): ``<folder containing the .exe>/data`` —
    never under ``sys._MEIPASS`` (temp extract), so uploaded logos persist
    and can be copied with the deploy folder.
    """
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(current_app.root_path)
    path = os.path.join(base, 'data')
    os.makedirs(path, exist_ok=True)
    return path


def site_upload_dir() -> str:
    path = os.path.join(app_persistent_data_dir(), SITE_UPLOAD_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def seed_site_content():
    changed = False
    for key, default in CMS_KEYS.items():
        if SiteSetting.query.get(key) is None:
            db.session.add(SiteSetting(section_key=key, data=copy.deepcopy(default)))
            changed = True
    if changed:
        db.session.commit()


def get_setting(key: str, default=None):
    row = SiteSetting.query.get(key)
    if row and row.data is not None:
        return copy.deepcopy(row.data)
    if default is not None:
        return copy.deepcopy(default)
    return copy.deepcopy(CMS_KEYS.get(key, {}))


def save_setting(key: str, data: dict):
    row = SiteSetting.query.get(key)
    if row is None:
        row = SiteSetting(section_key=key)
    row.data = data
    row.updated_at = datetime.utcnow()
    db.session.add(row)
    db.session.commit()
    return row.data


def get_branding():
    return get_setting('branding')


def get_landing_cms():
    splash = get_setting('landing_splash')
    # Migrate prior short default (1.3s) to the 3s splash duration.
    if isinstance(splash, dict) and int(splash.get('duration_ms') or 0) == 1300:
        splash = {**splash, 'duration_ms': 3000}
        save_setting('landing_splash', splash)
    hero = get_setting('landing_hero')
    # Migrate legacy hero related link label HRMS → eRMT (opens module after login).
    if isinstance(hero, dict) and isinstance(hero.get('related_links'), list):
        links = hero['related_links']
        changed = False
        new_links = []
        for link in links:
            if not isinstance(link, dict):
                new_links.append(link)
                continue
            label = (link.get('label') or '').strip()
            url = (link.get('url') or '').strip()
            if label.upper() == 'HRMS' or (
                label.upper() == 'ERMT' and url in ('/login', '/login/')
            ):
                updated = {
                    **link,
                    'label': 'eRMT',
                    'url': '/login?next=/ermt/dashboard',
                    'external': False,
                }
                if updated != link:
                    changed = True
                new_links.append(updated)
            else:
                new_links.append(link)
        if changed:
            hero = {**hero, 'related_links': new_links}
            save_setting('landing_hero', hero)
    return {
        'hero': hero,
        'services': get_setting('landing_services'),
        'mission': get_setting('landing_mission'),
        'stats': get_setting('landing_stats'),
        'splash': splash if isinstance(splash, dict) else get_setting('landing_splash'),
        'carousel': get_setting('landing_carousel'),
        'footer': get_setting('landing_footer'),
    }


def branding_logo_url(branding: dict | None = None) -> str:
    b = branding or get_branding()
    if b.get('use_uploaded_logo') and b.get('uploaded_logo'):
        return url_for('routes.site_media', filename=b['uploaded_logo'])
    static_name = b.get('logo_path') or 'hrmdo_logo_transparent.png'
    return url_for('static', filename=static_name)


def branding_seal_url(branding: dict | None = None) -> str:
    b = branding or get_branding()
    if b.get('use_uploaded_seal') and b.get('uploaded_seal'):
        return url_for('routes.site_media', filename=b['uploaded_seal'])
    static_name = b.get('seal_path') or 'jagna_logo.png'
    return url_for('static', filename=static_name)


def _pixel_is_checker_or_matte(r: int, g: int, b: int, a: int) -> bool:
    """True for transparency-preview checkerboard tiles or solid black/white matte."""
    if a < 24:
        return True
    # Classic editor transparency checkerboard (light gray / white tiles)
    if abs(r - g) <= 18 and abs(g - b) <= 18:
        if 218 <= r <= 255 and 218 <= g <= 255 and 218 <= b <= 255:
            return True
        if 160 <= r <= 220 and 160 <= g <= 220 and 160 <= b <= 220:
            return True
    # Solid black / near-black matte used instead of alpha
    if r <= 22 and g <= 22 and b <= 22:
        return True
    return False


def _pixel_is_checkerboard_only(r: int, g: int, b: int, a: int) -> bool:
    """Baked editor checkerboard (not seal shadows / bronze)."""
    if a < 24:
        return False
    if abs(r - g) > 18 or abs(g - b) > 18:
        return False
    if 218 <= r <= 255 and 218 <= g <= 255 and 218 <= b <= 255:
        return True
    if 160 <= r <= 220 and 160 <= g <= 220 and 160 <= b <= 220:
        return True
    return False


def logo_file_needs_normalize(path: str) -> bool:
    """Detect baked checkerboard/matte or large transparent padding."""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return False
    try:
        img = Image.open(path)
        img.load()
        img = img.convert('RGBA')
    except Exception:
        return False
    w, h = img.size
    if w < 8 or h < 8:
        return False
    px = img.load()
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (0, h // 2)):
        r, g, b, a = px[x, y]
        if a >= 24 and _pixel_is_checker_or_matte(r, g, b, a):
            return True
    bg = Image.new('RGBA', img.size, (0, 0, 0, 0))
    bbox = ImageChops.difference(img, bg).getbbox()
    if not bbox:
        return False
    left, top, right, bottom = bbox
    # More than ~6% empty margin on any side → trim worthwhile
    if left > w * 0.06 or top > h * 0.06 or (w - right) > w * 0.06 or (h - bottom) > h * 0.06:
        return True
    return False


def normalize_branding_logo_file(path: str) -> str:
    """
    Make uploaded logos web-safe for dark gradients:

    - Remove editor checkerboard / black matte corners (common false 'transparency')
    - Trim empty padding so the seal fills the hero medallion
    - Always write an RGBA PNG (replacing the original path when needed)
    """
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return path

    if not path or not os.path.isfile(path):
        return path

    if not logo_file_needs_normalize(path):
        # Still ensure PNG for JPEG branding uploads referenced by basename.
        if path.lower().endswith('.png'):
            return path
        try:
            img = Image.open(path).convert('RGBA')
            out_path = os.path.splitext(path)[0] + '.png'
            img.save(out_path, format='PNG', optimize=True)
            if out_path != path:
                try:
                    os.remove(path)
                except OSError:
                    pass
            return out_path
        except Exception:
            return path

    try:
        img = Image.open(path)
        img.load()
    except Exception:
        return path

    img = img.convert('RGBA')
    pixels = img.load()
    w, h = img.size
    if w < 8 or h < 8:
        return path

    # Clear matte / checkerboard starting from the four corners (flood-fill).
    visited = bytearray(w * h)
    stack = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        idx = y * w + x
        if visited[idx]:
            continue
        visited[idx] = 1
        r, g, b, a = pixels[x, y]
        if not _pixel_is_checker_or_matte(r, g, b, a):
            continue
        pixels[x, y] = (0, 0, 0, 0)
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    # Soft fringe for checkerboard tiles only (avoid chewing dark seal rim/shadows).
    fringe = []
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if not _pixel_is_checkerboard_only(r, g, b, a):
                continue
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h and pixels[nx, ny][3] == 0:
                    fringe.append((x, y))
                    break
    for x, y in fringe:
        pixels[x, y] = (0, 0, 0, 0)

    bg = Image.new('RGBA', img.size, (0, 0, 0, 0))
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()
    if bbox:
        pad = max(2, min(w, h) // 200)
        left = max(0, bbox[0] - pad)
        top = max(0, bbox[1] - pad)
        right = min(w, bbox[2] + pad)
        bottom = min(h, bbox[3] + pad)
        img = img.crop((left, top, right, bottom))

    out_path = os.path.splitext(path)[0] + '.png'
    img.save(out_path, format='PNG', optimize=True)
    if out_path != path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return out_path


def ensure_branding_uploads_normalized() -> None:
    """
    Re-normalize active portal logo/seal files (fixes baked checkerboard / padding
    already stored on disk without requiring a re-upload).
    """
    data = get_branding()
    changed = False
    for field in ('uploaded_logo', 'uploaded_seal'):
        name = (data.get(field) or '').strip()
        if not name:
            continue
        src = os.path.join(site_upload_dir(), name)
        if not os.path.isfile(src):
            continue
        new_path = normalize_branding_logo_file(src)
        new_name = os.path.basename(new_path)
        if new_name != name:
            data[field] = new_name
            changed = True
    if changed:
        save_setting('branding', data)


def save_branding_upload(file_storage, field: str) -> str:
    if not file_storage or not file_storage.filename:
        raise ValueError('No file selected.')
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        raise ValueError(f'Unsupported file type. Allowed: {", ".join(sorted(ALLOWED_LOGO_EXTENSIONS))}')
    safe = secure_filename(file_storage.filename)
    stamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    filename = f'{field}_{stamp}_{safe}'
    dest = os.path.join(site_upload_dir(), filename)
    file_storage.save(dest)
    normalized = normalize_branding_logo_file(dest)
    return os.path.basename(normalized)

def update_branding_from_form(form, files):
    data = get_branding()
    for key in ('org_name', 'portal_name', 'org_location', 'tagline', 'logo_path', 'seal_path'):
        if key in form:
            data[key] = (form.get(key) or '').strip()
    data['use_uploaded_logo'] = form.get('use_uploaded_logo') == '1'
    data['use_uploaded_seal'] = form.get('use_uploaded_seal') == '1'
    if files.get('uploaded_logo'):
        data['uploaded_logo'] = save_branding_upload(files['uploaded_logo'], 'logo')
        data['use_uploaded_logo'] = True
    if files.get('uploaded_seal'):
        data['uploaded_seal'] = save_branding_upload(files['uploaded_seal'], 'seal')
        data['use_uploaded_seal'] = True
    save_setting('branding', data)
    return data


def update_landing_hero_from_form(form):
    data = get_setting('landing_hero')
    data['title_primary'] = (form.get('title_primary') or '').strip()
    data['title_secondary'] = (form.get('title_secondary') or '').strip()
    data['subtitle'] = (form.get('subtitle') or '').strip()
    data['cta_label'] = (form.get('cta_label') or 'Employee Sign-In').strip()
    links = []
    labels = form.getlist('related_label')
    urls = form.getlist('related_url')
    externals = form.getlist('related_external')
    for i, label in enumerate(labels):
        label = (label or '').strip()
        url = (urls[i] if i < len(urls) else '').strip()
        if not label and not url:
            continue
        links.append({
            'label': label or 'Link',
            'url': url or '#',
            'external': (externals[i] if i < len(externals) else '') == '1',
        })
    if links:
        data['related_links'] = links
    save_setting('landing_hero', data)
    return data


def update_landing_services_from_form(form):
    data = get_setting('landing_services')
    data['section_title'] = (form.get('section_title') or '').strip()
    data['section_lead'] = (form.get('section_lead') or '').strip()
    cards = []
    titles = form.getlist('card_title')
    texts = form.getlist('card_text')
    icons = form.getlist('card_icon')
    for i, title in enumerate(titles):
        title = (title or '').strip()
        text = (texts[i] if i < len(texts) else '').strip()
        icon = (icons[i] if i < len(icons) else '').strip()
        if not title and not text:
            continue
        payload = {'title': title, 'text': text}
        if icon:
            payload['icon'] = icon
        cards.append(payload)
    if cards:
        data['cards'] = cards
    save_setting('landing_services', data)
    return data


def update_landing_mission_from_form(form):
    data = get_setting('landing_mission')
    data['title'] = (form.get('title') or '').strip()
    data['text'] = (form.get('text') or '').strip()
    save_setting('landing_mission', data)
    return data


def update_landing_stats_from_form(form):
    data = get_setting('landing_stats')
    data['section_title'] = (form.get('section_title') or '').strip()
    data['section_lead'] = (form.get('section_lead') or '').strip()
    save_setting('landing_stats', data)
    return data


def update_landing_splash_from_form(form):
    data = get_setting('landing_splash')
    data['enabled'] = form.get('enabled') == '1'
    data['show_tagline'] = form.get('show_tagline') == '1'
    data['tagline'] = (form.get('tagline') or '').strip()
    data['bar_aria_label'] = (form.get('bar_aria_label') or 'Loading').strip()
    try:
        data['duration_ms'] = max(500, min(10000, int(form.get('duration_ms') or 3000)))
    except (TypeError, ValueError):
        data['duration_ms'] = 3000
    save_setting('landing_splash', data)
    return data


def update_landing_carousel_from_form(form):
    data = get_setting('landing_carousel')
    data['enabled'] = form.get('enabled') == '1'
    data['section_label'] = (form.get('section_label') or 'Announcements').strip()
    try:
        data['interval_ms'] = max(2000, min(60000, int(form.get('interval_ms') or 6000)))
    except (TypeError, ValueError):
        data['interval_ms'] = 6000
    slides = []
    titles = form.getlist('slide_title')
    texts = form.getlist('slide_text')
    link_urls = form.getlist('slide_link_url')
    link_labels = form.getlist('slide_link_label')
    for i, title in enumerate(titles):
        title = (title or '').strip()
        text = (texts[i] if i < len(texts) else '').strip()
        if not title and not text:
            continue
        slides.append({
            'title': title,
            'text': text,
            'link_url': (link_urls[i] if i < len(link_urls) else '').strip(),
            'link_label': (link_labels[i] if i < len(link_labels) else '').strip(),
        })
    if slides:
        data['slides'] = slides
    save_setting('landing_carousel', data)
    return data


def update_landing_footer_from_form(form):
    data = get_setting('landing_footer')
    for key in (
        'column1_title', 'column2_title', 'seal_caption', 'copyright', 'disclaimer',
        'column1_lines', 'column2_paragraphs',
    ):
        if key in form:
            val = form.get(key) or ''
            if key in ('column1_lines', 'column2_paragraphs'):
                data[key] = [ln.strip() for ln in val.splitlines() if ln.strip()]
            else:
                data[key] = val.strip()
    links = []
    labels = form.getlist('gov_label')
    urls = form.getlist('gov_url')
    for i, label in enumerate(labels):
        label = (label or '').strip()
        url = (urls[i] if i < len(urls) else '').strip()
        if label and url:
            links.append({'label': label, 'url': url})
    if links:
        data['gov_links'] = links
    save_setting('landing_footer', data)
    return data

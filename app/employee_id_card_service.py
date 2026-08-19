"""Generate printable LGU Jagna employee ID cards from official PDF templates."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from app.employee_id_photo_service import read_employee_id_photo

# Template page sizes (portrait ID).
PLANTILLA_TEMPLATE_REL = Path('static') / 'id_templates' / 'lgu_jagna_id.pdf'  # jagna plantilla id.pdf
JO_COS_TEMPLATE_REL = Path('static') / 'id_templates' / 'lgu_jagna_jo_id.pdf'

# JO/COS photo + municipal seal (template base positions before vertical centering).
JO_COS_SEAL_BASE = fitz.Rect(0.53, 86.53, 128.15, 214.14)
JO_COS_PHOTO_BASE = fitz.Rect(117.0, 86.7, 245.3, 215.0)
JO_COS_SHADOW_BASE = fitz.Rect(108.5, 78.47, 259.81, 229.78)

# JO/COS template (LGU JAGNA JO ID.pdf) — page ≈ 259.5 × 331.5.
# Logo-picture + two-column stack are shifted down so the pair is vertically
# centered in the body below the green header (~73.5).
JO_COS_HEADER_BOTTOM = 73.5
JO_COS_PAGE_BOTTOM = 331.5
# Two-column band: name / dept / ID NO on the left; QR on the right.
JO_COS_QR_SIZE = 68.0
JO_COS_CONTENT_RIGHT = 250.0
JO_COS_TEXT_LEFT = 12.0
JO_COS_TEXT_RIGHT = JO_COS_CONTENT_RIGHT - JO_COS_QR_SIZE - 8.0  # ≈ 174
JO_COS_QR_LEFT = JO_COS_TEXT_RIGHT + 8.0  # ≈ 182
_JO_COS_QR_TOP_BASE = 230.0
_JO_COS_NAME_TOP_BASE = 228.0
_JO_COS_DEPT_TOP_BASE = 252.0
_JO_COS_ID_TOP_BASE = 280.0
_JO_COS_ID_BOTTOM_BASE = 302.0
_JO_COS_STACK_TOP = JO_COS_SHADOW_BASE.y0  # include photo drop-shadow under the header
_JO_COS_STACK_BOTTOM = _JO_COS_ID_BOTTOM_BASE
_JO_COS_STACK_H = _JO_COS_STACK_BOTTOM - _JO_COS_STACK_TOP
_JO_COS_BODY_H = JO_COS_PAGE_BOTTOM - JO_COS_HEADER_BOTTOM
JO_COS_SHIFT_Y = JO_COS_HEADER_BOTTOM + (_JO_COS_BODY_H - _JO_COS_STACK_H) / 2 - _JO_COS_STACK_TOP


def _rect_shift_y(rect: fitz.Rect, dy: float) -> fitz.Rect:
    return fitz.Rect(rect.x0, rect.y0 + dy, rect.x1, rect.y1 + dy)


JO_COS_PHOTO_RECT = _rect_shift_y(JO_COS_PHOTO_BASE, JO_COS_SHIFT_Y)
JO_COS_SEAL_RECT = _rect_shift_y(JO_COS_SEAL_BASE, JO_COS_SHIFT_Y)
JO_COS_QR_RECT = fitz.Rect(
    JO_COS_QR_LEFT,
    _JO_COS_QR_TOP_BASE + JO_COS_SHIFT_Y,
    JO_COS_QR_LEFT + JO_COS_QR_SIZE,
    _JO_COS_QR_TOP_BASE + JO_COS_SHIFT_Y + JO_COS_QR_SIZE,
)
JO_COS_NAME_RECT = fitz.Rect(
    JO_COS_TEXT_LEFT,
    _JO_COS_NAME_TOP_BASE + JO_COS_SHIFT_Y,
    JO_COS_TEXT_RIGHT,
    _JO_COS_NAME_TOP_BASE + JO_COS_SHIFT_Y + 26.0,
)
JO_COS_DEPT_RECT = fitz.Rect(
    JO_COS_TEXT_LEFT,
    _JO_COS_DEPT_TOP_BASE + JO_COS_SHIFT_Y,
    JO_COS_TEXT_RIGHT,
    _JO_COS_DEPT_TOP_BASE + JO_COS_SHIFT_Y + 24.0,
)
JO_COS_ID_BOX = fitz.Rect(
    JO_COS_TEXT_LEFT,
    _JO_COS_ID_TOP_BASE + JO_COS_SHIFT_Y,
    JO_COS_TEXT_RIGHT,
    _JO_COS_ID_BOTTOM_BASE + JO_COS_SHIFT_Y,
)
# Baked-in centered "ID NO:" box on the template — erase before redrawing on the right.
JO_COS_TEMPLATE_ID_ERASE = fitz.Rect(78.0, 306.5, 182.0, 329.0)
JO_COS_ID_MAX_SIZE = 12.5
JO_COS_ID_MIN_SIZE = 8.0
# Glyph advance multiplier for name tracking (< 1.0 = tighter; closer to 1.0 = more open).
JO_COS_NAME_TRACKING = 0.94
JO_COS_NAME_MAX_SIZE = 16.0
JO_COS_NAME_MIN_SIZE = 9.5

# Plantilla template (jagna plantilla id.pdf): page ≈ 288 × 396 pt.
# Photo: 3.5 cm × 4.5 cm, centered; raised for gap above the QR / identity block.
_CM = 72.0 / 2.54  # points per centimeter
PLANTILLA_PHOTO_W = 3.5 * _CM  # ≈ 99.21 pt
PLANTILLA_PHOTO_H = 4.5 * _CM  # ≈ 127.56 pt
PLANTILLA_PHOTO_CENTER_X = 144.0
PLANTILLA_PHOTO_TOP = 118.0  # raised to widen gap above the QR / identity block
PLANTILLA_PHOTO_RECT = fitz.Rect(
    PLANTILLA_PHOTO_CENTER_X - PLANTILLA_PHOTO_W / 2,
    PLANTILLA_PHOTO_TOP,
    PLANTILLA_PHOTO_CENTER_X + PLANTILLA_PHOTO_W / 2,
    PLANTILLA_PHOTO_TOP + PLANTILLA_PHOTO_H,
)
# Match template frame thickness (outer ≈76–212, inner white ≈91–197).
PLANTILLA_FRAME_BORDER_PT = 3.25  # 1/4 of previous 13 pt frame
PLANTILLA_FRAME_GOLD = (0.83, 0.68, 0.22)  # light gold (top/left highlight)
PLANTILLA_FRAME_GOLD_DARK = (0.62, 0.48, 0.10)  # darker gold (bottom/right)
# Text below photo — two-column: name / dept / ID NO left; QR right (page ≈ 288 × 396).
PLANTILLA_QR_SIZE = 72.0
PLANTILLA_CONTENT_RIGHT = 274.0
PLANTILLA_TEXT_LEFT = 14.0
PLANTILLA_TEXT_RIGHT = PLANTILLA_CONTENT_RIGHT - PLANTILLA_QR_SIZE - 10.0  # ≈ 192
PLANTILLA_QR_LEFT = PLANTILLA_TEXT_RIGHT + 10.0  # ≈ 202
PLANTILLA_QR_TOP = 282.0
PLANTILLA_QR_RECT = fitz.Rect(
    PLANTILLA_QR_LEFT,
    PLANTILLA_QR_TOP,
    PLANTILLA_QR_LEFT + PLANTILLA_QR_SIZE,
    PLANTILLA_QR_TOP + PLANTILLA_QR_SIZE,
)
PLANTILLA_NAME_RECT = fitz.Rect(PLANTILLA_TEXT_LEFT, 280.0, PLANTILLA_TEXT_RIGHT, 308.0)
PLANTILLA_DEPT_RECT = fitz.Rect(PLANTILLA_TEXT_LEFT, 306.0, PLANTILLA_TEXT_RIGHT, 332.0)
PLANTILLA_ID_BOX = fitz.Rect(PLANTILLA_TEXT_LEFT, 336.0, PLANTILLA_TEXT_RIGHT, 360.0)
PLANTILLA_ID_MAX_SIZE = 13.0
PLANTILLA_ID_MIN_SIZE = 8.0
PLANTILLA_NAME_TRACKING = 0.94
PLANTILLA_NAME_MAX_SIZE = 18.0
PLANTILLA_NAME_MIN_SIZE = 10.0

_STATIC_ROOT = Path(__file__).resolve().parent / 'static' / 'id_templates'
_ARCHIVO_BLACK_CANDIDATES = (
    _STATIC_ROOT / 'fonts' / 'ArchivoBlack-Regular.ttf',
    Path(r'C:\Windows\Fonts\ArchivoBlack-Regular.ttf'),
)
_ARIAL_CANDIDATES = (
    Path(r'C:\Windows\Fonts\arial.ttf'),
    Path(r'C:\Windows\Fonts\Arial.ttf'),
    Path('/usr/share/fonts/truetype/msttcorefonts/Arial.ttf'),
    Path('/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'),
)
_ARIAL_BOLD_CANDIDATES = (
    Path(r'C:\Windows\Fonts\arialbd.ttf'),
    Path(r'C:\Windows\Fonts\Arialbd.ttf'),
    Path('/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf'),
    Path('/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'),
)


def _archivo_black_fontfile() -> str | None:
    """Same display face as the header word JAGNA on the JO ID template."""
    for path in _ARCHIVO_BLACK_CANDIDATES:
        if path.is_file():
            return str(path)
    return None


def _arial_fontfile(*, bold: bool = False) -> str | None:
    candidates = _ARIAL_BOLD_CANDIDATES if bold else _ARIAL_CANDIDATES
    for path in candidates:
        if path.is_file():
            return str(path)
    if bold:
        return _arial_fontfile(bold=False)
    return None


def is_jo_cos_employee(employee) -> bool:
    text = (
        (getattr(employee, 'status_of_appointment', None) or '')
        + ' '
        + (getattr(employee, 'nature_of_appointment', None) or '')
    ).strip().lower()
    return ('job order' in text) or ('contract of service' in text) or ('cos' in text) or text == 'jo'


def id_card_format_label(employee) -> str:
    return 'JO/COS' if is_jo_cos_employee(employee) else 'Plantilla'


def id_template_path_for_employee(employee) -> Path:
    root = Path(__file__).resolve().parent
    rel = JO_COS_TEMPLATE_REL if is_jo_cos_employee(employee) else PLANTILLA_TEMPLATE_REL
    return root / rel


def format_jo_cos_id_name(employee) -> str:
    """JO/COS card: FIRSTNAME LASTNAME [EXT] on one line (no middle name), all caps."""
    first = (employee.first_name or '').strip().upper()
    last = (employee.last_name or '').strip().upper()
    ext = ''
    normalize = getattr(type(employee), 'normalize_name_extension', None)
    if callable(normalize):
        ext = normalize(getattr(employee, 'name_extension', None)).upper()
    else:
        ext = (getattr(employee, 'name_extension', None) or '').strip().upper()
    name = ' '.join(p for p in (first, last, ext) if p)
    return name or '—'


def format_plantilla_id_name(employee) -> str:
    """Plantilla card: FIRSTNAME M.I. LASTNAME [EXT] (all caps)."""
    first = (employee.first_name or '').strip().upper()
    last = (employee.last_name or '').strip().upper()
    middle = (employee.middle_name or '').strip()
    normalize = getattr(type(employee), 'normalize_name_extension', None)
    if callable(normalize):
        ext = normalize(getattr(employee, 'name_extension', None)).upper()
    else:
        ext = (getattr(employee, 'name_extension', None) or '').strip().upper()
    parts = []
    if first:
        parts.append(first)
    if middle:
        parts.append(f'{middle[0].upper()}.')
    if last:
        parts.append(last)
    if ext:
        parts.append(ext)
    return ' '.join(parts) if parts else '—'


def format_id_card_department(employee) -> str:
    """JO/COS ID line: prefer department description, else department name."""
    dept = getattr(employee, 'department', None)
    if not dept:
        return '—'
    desc = (getattr(dept, 'description', None) or '').strip()
    name = (getattr(dept, 'name', None) or '').strip()
    label = desc or name
    return label.upper() if label else '-'


def _fit_textbox(
    page,
    rect: fitz.Rect,
    text: str,
    *,
    max_size: float,
    min_size: float,
    bold: bool = False,
    use_arial: bool = False,
):
    """Draw centered text, shrinking font until it fits the rect."""
    fontname = 'hebo' if bold else 'helv'
    if use_arial:
        fontfile = _arial_fontfile(bold=bold)
        if fontfile:
            fontname = 'arial_bold' if bold else 'arial'
            page.insert_font(fontname=fontname, fontfile=fontfile)

    size = max_size
    while size >= min_size:
        rc = page.insert_textbox(
            rect,
            text,
            fontsize=size,
            fontname=fontname,
            align=fitz.TEXT_ALIGN_CENTER,
            color=(0, 0, 0),
        )
        if rc >= 0:
            return size
        size -= 0.5
    page.insert_textbox(
        rect,
        text,
        fontsize=min_size,
        fontname=fontname,
        align=fitz.TEXT_ALIGN_CENTER,
        color=(0, 0, 0),
    )
    return min_size


def _photo_bytes_for_id_slot(data: bytes, width_pt: float, height_pt: float) -> bytes:
    """Crop/resize image to fill the ID photo slot (cover), return JPEG bytes."""
    from io import BytesIO

    from PIL import Image

    im = Image.open(BytesIO(data))
    if im.mode not in ('RGB', 'L'):
        im = im.convert('RGB')
    elif im.mode == 'L':
        im = im.convert('RGB')

    # Target pixel size at ~150 dpi for sharp print in the slot
    dpi = 150.0
    tw = max(1, int(round(width_pt * dpi / 72.0)))
    th = max(1, int(round(height_pt * dpi / 72.0)))
    src_w, src_h = im.size
    scale = max(tw / src_w, th / src_h)
    nw, nh = max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale)))
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    left = max(0, (nw - tw) // 2)
    top = max(0, (nh - th) // 2)
    im = im.crop((left, top, left + tw, top + th))
    out = BytesIO()
    im.save(out, format='JPEG', quality=92, optimize=True)
    return out.getvalue()


def _condensed_line_width(font, text: str, size: float, tracking: float) -> float:
    widths = [font.text_length(ch, fontsize=size) for ch in text]
    if not widths:
        return 0.0
    if len(widths) == 1:
        return widths[0]
    return sum(widths[:-1]) * tracking + widths[-1]


def _draw_condensed_text(
    page,
    rect: fitz.Rect,
    text: str,
    *,
    fontname: str,
    fontfile: str | None,
    max_size: float,
    min_size: float,
    tracking: float = JO_COS_NAME_TRACKING,
    align: str = 'center',
) -> float:
    """
    Draw one line of text in rect with condensed letter spacing.
    tracking < 1.0 tightens glyph advances. align: 'left' | 'center' | 'right'.
    """
    text = text or '—'
    try:
        font = fitz.Font(fontfile=fontfile) if fontfile else fitz.Font(fontname='hebo')
    except Exception:
        font = fitz.Font(fontname='hebo')

    size = max_size
    while size >= min_size:
        if _condensed_line_width(font, text, size, tracking) <= rect.width - 1.0:
            break
        size -= 0.5
    size = max(size, min_size)

    widths = [font.text_length(ch, fontsize=size) for ch in text]
    total = _condensed_line_width(font, text, size, tracking)
    if align == 'left':
        x = rect.x0
    elif align == 'right':
        x = rect.x1 - total
    else:
        x = (rect.x0 + rect.x1 - total) / 2
    baseline = (rect.y0 + rect.y1) / 2 + size * 0.32
    for i, ch in enumerate(text):
        page.insert_text(
            (x, baseline),
            ch,
            fontsize=size,
            fontname=fontname,
            color=(0, 0, 0),
        )
        advance = widths[i] if i == len(text) - 1 else widths[i] * tracking
        x += advance
    return size


def _draw_condensed_centered_text(
    page,
    rect: fitz.Rect,
    text: str,
    *,
    fontname: str,
    fontfile: str | None,
    max_size: float,
    min_size: float,
    tracking: float = JO_COS_NAME_TRACKING,
) -> float:
    """Back-compat wrapper: centered condensed one-liner."""
    return _draw_condensed_text(
        page,
        rect,
        text,
        fontname=fontname,
        fontfile=fontfile,
        max_size=max_size,
        min_size=min_size,
        tracking=tracking,
        align='center',
    )


def _employee_id_qr_png(employee_id: str) -> bytes:
    """PNG bytes for a QR encoding the employee badge / ID number."""
    from io import BytesIO

    import qrcode

    payload = (employee_id or '').strip() or '—'
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=1,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _draw_qr_identity_block(
    page,
    *,
    qr_rect: fitz.Rect,
    name_rect: fitz.Rect,
    dept_rect: fitz.Rect,
    id_box: fitz.Rect,
    name: str,
    department: str,
    employee_id: str,
    name_font: str,
    name_fontfile: str | None,
    dept_font: str,
    id_font: str,
    name_max_size: float,
    name_min_size: float,
    name_tracking: float,
    id_max_size: float,
    id_min_size: float,
    id_label: str = 'ID NO:',
    arial_bold_path: str | None = None,
) -> bool:
    """
    Two-column identity band: QR (employee ID) on the left;
    name / department / centered ID NO box on the right.
    Returns True if the QR image was placed.
    """
    emp_id = (employee_id or '').strip() or '—'
    has_qr = False
    try:
        page.insert_image(
            qr_rect,
            stream=_employee_id_qr_png(emp_id),
            keep_proportion=True,
            overlay=True,
        )
        has_qr = True
    except Exception:
        has_qr = False

    def _fit_left(rect, text, max_size, min_size, font):
        size = max_size
        while size >= min_size:
            rc = page.insert_textbox(
                rect, text, fontsize=size, fontname=font,
                align=fitz.TEXT_ALIGN_LEFT, color=(0, 0, 0),
            )
            if rc >= 0:
                return size
            size -= 0.5
        page.insert_textbox(
            rect, text, fontsize=min_size, fontname=font,
            align=fitz.TEXT_ALIGN_LEFT, color=(0, 0, 0),
        )
        return min_size

    _draw_condensed_text(
        page,
        name_rect,
        name,
        fontname=name_font,
        fontfile=name_fontfile,
        max_size=name_max_size,
        min_size=name_min_size,
        tracking=name_tracking,
        align='left',
    )
    _fit_left(dept_rect, department, 11.0, 7.5, dept_font)

    page.draw_rect(id_box, color=(0, 0, 0), width=0.8, fill=(1, 1, 1), overlay=True)
    id_line = f'{id_label} {emp_id}'
    try:
        measure = (
            fitz.Font(fontfile=arial_bold_path) if arial_bold_path
            else fitz.Font(fontname='hebo')
        )
    except Exception:
        measure = fitz.Font(fontname='hebo')
    size = id_max_size
    max_w = id_box.width - 8.0
    while size >= id_min_size:
        if measure.text_length(id_line, fontsize=size) <= max_w:
            break
        size -= 0.4
    size = max(size, id_min_size)
    tw = measure.text_length(id_line, fontsize=size)
    x = id_box.x0 + (id_box.width - tw) / 2
    baseline = (id_box.y0 + id_box.y1) / 2 + size * 0.30
    page.insert_text(
        (x, baseline),
        id_line,
        fontsize=size,
        fontname=id_font,
        color=(0, 0, 0),
    )
    return has_qr


def _find_page_image_xref(page, target: fitz.Rect, *, tol: float = 2.5) -> tuple[int | None, int | None]:
    """Return (image_xref, soft_mask_xref) for an image whose placement matches target."""
    for img in page.get_images(full=True):
        xref = img[0]
        smask = img[1] if len(img) > 1 else 0
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        for r in rects:
            if (
                abs(r.x0 - target.x0) <= tol
                and abs(r.y0 - target.y0) <= tol
                and abs(r.x1 - target.x1) <= tol
                and abs(r.y1 - target.y1) <= tol
            ):
                return xref, (smask or None)
    return None, None


def _jo_cos_clean_seal_png(doc, seal_xref: int, smask_xref: int | None) -> bytes:
    """
    Flatten the municipal seal onto pure white with no drop-shadow.

    The template seal is CMYK + soft-mask; the mask fringe renders as a brown
    shadow after repositioning. Harden the mask and bleach residual gray rim.
    """
    from io import BytesIO

    import numpy as np
    from PIL import Image, ImageFilter

    pix = fitz.Pixmap(doc, seal_xref)
    try:
        if pix.colorspace is not None and pix.colorspace.n == 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        seal = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    finally:
        pix = None

    if smask_xref:
        mpix = fitz.Pixmap(doc, smask_xref)
        try:
            mask = Image.frombytes('L', (mpix.width, mpix.height), mpix.samples)
        finally:
            mpix = None
    else:
        mask = Image.new('L', seal.size, 255)

    # Soft-mask fringe is the drop shadow — force a hard cutout, then erode
    # a couple of pixels so baked-in brown rim glow does not remain.
    mask_h = mask.point(lambda p: 255 if p >= 248 else 0)
    mask_h = mask_h.filter(ImageFilter.MinFilter(5))
    mask_h = mask_h.filter(ImageFilter.MaxFilter(3))  # smooth after erode
    out = Image.new('RGB', seal.size, (255, 255, 255))
    out.paste(seal, mask=mask_h)

    arr = np.asarray(out).copy()
    m = np.asarray(mask_h)
    ys, xs = np.where(m >= 255)
    if xs.size:
        cy = (float(ys.min()) + float(ys.max())) / 2.0
        cx = (float(xs.min()) + float(xs.max())) / 2.0
        ry = max((float(ys.max()) - float(ys.min())) / 2.0, 1.0)
        rx = max((float(xs.max()) - float(xs.min())) / 2.0, 1.0)
        yy, xx = np.ogrid[: m.shape[0], : m.shape[1]]
        dist = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
        keep = (m > 0) & (dist <= 0.995)
        arr[~keep] = 255
        r = arr[:, :, 0].astype(np.int16)
        g = arr[:, :, 1].astype(np.int16)
        b = arr[:, :, 2].astype(np.int16)
        lum = (r.astype(np.float32) + g + b) / 3.0
        sat = (np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)).astype(
            np.float32
        )
        # Near-white / muddy fringe only (not saturated rope greens/oranges).
        muddy = keep & (dist > 0.88) & (
            ((lum > 150) & (lum < 235) & (sat < 70))
            | ((lum < 130) & (sat < 50))
        )
        arr[muddy] = 255
        out = Image.fromarray(arr)

    buf = BytesIO()
    out.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def _soft_drop_shadow_png(
    *,
    width_pt: float,
    height_pt: float,
    shape: str = 'rect',
    blur_pt: float = 3.2,
    opacity: float = 0.38,
    dpi: float = 150.0,
) -> bytes:
    """
    Soft black drop-shadow PNG (RGBA) sized to the content box plus blur padding.
    shape: 'rect' for the photo frame, 'ellipse' for the municipal seal.
    """
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFilter

    scale = dpi / 72.0
    blur_px = max(1, int(round(blur_pt * scale)))
    pad = blur_px * 3
    cw = max(1, int(round(width_pt * scale)))
    ch = max(1, int(round(height_pt * scale)))
    canvas = Image.new('L', (cw + pad * 2, ch + pad * 2), 0)
    draw = ImageDraw.Draw(canvas)
    box = (pad, pad, pad + cw - 1, pad + ch - 1)
    if shape == 'ellipse':
        draw.ellipse(box, fill=255)
    else:
        draw.rectangle(box, fill=255)
    canvas = canvas.filter(ImageFilter.GaussianBlur(radius=blur_px))
    # Tone down alpha for a print-friendly soft shadow (cool gray, not brown).
    alpha = canvas.point(lambda p: int(round(p * opacity)))
    rgba = Image.new('RGBA', canvas.size, (40, 40, 45, 0))
    rgba.putalpha(alpha)
    buf = BytesIO()
    rgba.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def _insert_soft_drop_shadow(
    page,
    content_rect: fitz.Rect,
    *,
    shape: str = 'rect',
    offset_x: float = 3.5,
    offset_y: float = 4.0,
    blur_pt: float = 3.2,
    opacity: float = 0.38,
) -> None:
    """Place a soft drop shadow under content_rect (offset down-right)."""
    shadow_png = _soft_drop_shadow_png(
        width_pt=content_rect.width,
        height_pt=content_rect.height,
        shape=shape,
        blur_pt=blur_pt,
        opacity=opacity,
    )
    pad = blur_pt * 3
    shadow_rect = fitz.Rect(
        content_rect.x0 + offset_x - pad,
        content_rect.y0 + offset_y - pad,
        content_rect.x1 + offset_x + pad,
        content_rect.y1 + offset_y + pad,
    )
    page.insert_image(shadow_rect, stream=shadow_png, keep_proportion=False, overlay=True)


def _jagna_seal_asset_path() -> Path | None:
    """High-quality municipal seal PNG (preferred over the dry template extract)."""
    candidates = (
        Path(__file__).resolve().parent / 'static' / 'id_templates' / 'jagna_seal.png',
        Path(__file__).resolve().parent / 'static' / 'jagna_logo.png',
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _jagna_seal_png_for_id() -> bytes | None:
    """Load the attached/official seal as PNG bytes, cropped to opaque content."""
    from io import BytesIO

    from PIL import Image

    path = _jagna_seal_asset_path()
    if path is None:
        return None
    im = Image.open(path).convert('RGBA')
    bbox = im.getbbox()
    if bbox:
        # Small pad so the rope edge is not clipped when scaled into the slot.
        pad = 4
        x0 = max(0, bbox[0] - pad)
        y0 = max(0, bbox[1] - pad)
        x1 = min(im.width, bbox[2] + pad)
        y1 = min(im.height, bbox[3] + pad)
        im = im.crop((x0, y0, x1, y1))
    buf = BytesIO()
    im.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def _reposition_jo_cos_logo_picture(page) -> None:
    """
    Move the municipal seal + photo slot down by JO_COS_SHIFT_Y so the
    logo-picture block clears the header and sits with the two-column band
    in a vertically centered body stack.
    """
    dy = JO_COS_SHIFT_Y
    if abs(dy) < 0.25:
        return

    seal_png = None
    try:
        seal_png = _jagna_seal_png_for_id()
    except Exception:
        seal_png = None
    if not seal_png:
        seal_xref, smask_xref = _find_page_image_xref(page, JO_COS_SEAL_BASE)
        if seal_xref is not None:
            try:
                seal_png = _jo_cos_clean_seal_png(page.parent, seal_xref, smask_xref)
            except Exception:
                seal_png = None

    # Blank the original seal (incl. soft-mask shadow) / photo silhouette.
    cover = fitz.Rect(
        min(JO_COS_SEAL_BASE.x0, JO_COS_SHADOW_BASE.x0) - 2.0,
        min(JO_COS_SEAL_BASE.y0, JO_COS_SHADOW_BASE.y0) - 2.0,
        max(JO_COS_SEAL_BASE.x1, JO_COS_SHADOW_BASE.x1) + 2.0,
        max(JO_COS_SEAL_BASE.y1, JO_COS_SHADOW_BASE.y1) + 2.0,
    )
    # Soft-mask drop shadow spills a few points past the seal bbox.
    seal_pad = fitz.Rect(
        JO_COS_SEAL_BASE.x0 - 4.0,
        JO_COS_SEAL_BASE.y0 - 4.0,
        JO_COS_SEAL_BASE.x1 + 10.0,
        JO_COS_SEAL_BASE.y1 + 12.0,
    )
    page.draw_rect(cover | seal_pad, color=None, fill=(1, 1, 1), overlay=True)

    # Official seal asset — no drop shadow (flat, crisp).
    if seal_png:
        page.insert_image(JO_COS_SEAL_RECT, stream=seal_png, keep_proportion=True, overlay=True)

    # Photo frame keeps a soft 3D shadow.
    _insert_soft_drop_shadow(
        page,
        JO_COS_PHOTO_RECT,
        shape='rect',
        offset_x=4.5,
        offset_y=5.0,
        blur_pt=3.4,
        opacity=0.45,
    )
    page.draw_rect(JO_COS_PHOTO_RECT, color=None, fill=(1, 1, 1), overlay=True)


def _overlay_jo_cos_page(page, employee) -> dict:
    """JO/COS layout: photo; QR (employee ID) left; name / dept / ID NO on the right."""
    status = {
        'has_photo': False,
        'has_qr': False,
        'name': format_jo_cos_id_name(employee),
        'department': format_id_card_department(employee),
        'employee_id': (employee.employee_id or '').strip(),
        'format': 'JO/COS',
    }

    _reposition_jo_cos_logo_picture(page)

    data, _mime, _rec = read_employee_id_photo(employee)
    if data:
        try:
            page.insert_image(JO_COS_PHOTO_RECT, stream=data, keep_proportion=True, overlay=True)
            status['has_photo'] = True
        except Exception:
            status['has_photo'] = False

    # Erase the template's centered ID NO box so it does not clash with the new layout.
    page.draw_rect(JO_COS_TEMPLATE_ID_ERASE, color=None, fill=(1, 1, 1), overlay=True)

    archivo = _archivo_black_fontfile()
    if archivo:
        page.insert_font(fontname='archivo_black', fontfile=archivo)

    arial = _arial_fontfile(bold=False)
    arial_bold = _arial_fontfile(bold=True)
    if arial:
        page.insert_font(fontname='arial', fontfile=arial)
    if arial_bold:
        page.insert_font(fontname='arial_bold', fontfile=arial_bold)

    status['has_qr'] = _draw_qr_identity_block(
        page,
        qr_rect=JO_COS_QR_RECT,
        name_rect=JO_COS_NAME_RECT,
        dept_rect=JO_COS_DEPT_RECT,
        id_box=JO_COS_ID_BOX,
        name=status['name'],
        department=status['department'],
        employee_id=status['employee_id'],
        name_font='archivo_black' if archivo else 'hebo',
        name_fontfile=archivo,
        dept_font='arial' if arial else 'helv',
        id_font='arial_bold' if arial_bold else 'hebo',
        name_max_size=JO_COS_NAME_MAX_SIZE,
        name_min_size=JO_COS_NAME_MIN_SIZE,
        name_tracking=JO_COS_NAME_TRACKING,
        id_max_size=JO_COS_ID_MAX_SIZE,
        id_min_size=JO_COS_ID_MIN_SIZE,
        arial_bold_path=arial_bold,
    )
    return status


def _plantilla_frame_outer(photo_rect: fitz.Rect) -> fitz.Rect:
    pad = PLANTILLA_FRAME_BORDER_PT
    return fitz.Rect(
        photo_rect.x0 - pad,
        photo_rect.y0 - pad,
        photo_rect.x1 + pad,
        photo_rect.y1 + pad,
    )


def _apply_plantilla_photo_and_frame(page, fitted_jpeg: bytes | None) -> None:
    """Composite photo + thin gold frame onto the page while keeping the watermark visible.

    Renders the template, replaces the old opaque frame/slot with a watermark patch,
    then pastes an RGBA photo/frame layer (transparent outside the frame).
    """
    from io import BytesIO

    from PIL import Image, ImageDraw

    scale = 3
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    base = Image.frombytes('RGB', (pix.width, pix.height), pix.samples).convert('RGBA')

    # Old template frame / photo-slot region (points → pixels).
    old = fitz.Rect(74.0, 88.0, 214.0, 256.0)
    ox0 = max(0, int(old.x0 * scale))
    oy0 = max(0, int(old.y0 * scale))
    ox1 = min(base.width, int(old.x1 * scale))
    oy1 = min(base.height, int(old.y1 * scale))

    # Cover the entire old template frame/slot with a light body fill.
    # Never paste a watermark crop — that duplicated the seal behind the photo.
    # The original full-page watermark stays intact outside this rectangle.
    fill = (250, 250, 250, 255)
    ImageDraw.Draw(base).rectangle((ox0, oy0, ox1 - 1, oy1 - 1), fill=fill)

    # Transparent layer: only photo + gold frame are opaque.
    layer = Image.new('RGBA', base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    outer = _plantilla_frame_outer(PLANTILLA_PHOTO_RECT)

    def box(rect: fitz.Rect) -> list[int]:
        return [
            int(round(rect.x0 * scale)),
            int(round(rect.y0 * scale)),
            int(round(rect.x1 * scale)),
            int(round(rect.y1 * scale)),
        ]

    shadow = fitz.Rect(outer.x0 + 2.5, outer.y0 + 2.5, outer.x1 + 2.5, outer.y1 + 2.5)
    draw.rectangle(box(shadow), fill=(120, 120, 125, 45))

    gold = tuple(int(round(c * 255)) for c in PLANTILLA_FRAME_GOLD) + (255,)
    dark = tuple(int(round(c * 255)) for c in PLANTILLA_FRAME_GOLD_DARK) + (255,)
    photo_box = box(PLANTILLA_PHOTO_RECT)

    if fitted_jpeg:
        photo = Image.open(BytesIO(fitted_jpeg)).convert('RGBA')
        photo = photo.resize(
            (max(1, photo_box[2] - photo_box[0]), max(1, photo_box[3] - photo_box[1])),
            Image.Resampling.LANCZOS,
        )
        layer.paste(photo, (photo_box[0], photo_box[1]), photo)
    else:
        draw.rectangle(photo_box, fill=(255, 255, 255, 255))

    ox0f, oy0f, ox1f, oy1f = tuple(outer)
    ix0, iy0, ix1, iy1 = tuple(PLANTILLA_PHOTO_RECT)
    draw.rectangle(box(fitz.Rect(ox0f, oy0f, ox1f, iy0)), fill=gold)
    draw.rectangle(box(fitz.Rect(ox0f, iy1, ox1f, oy1f)), fill=dark)
    draw.rectangle(box(fitz.Rect(ox0f, iy0, ix0, iy1)), fill=gold)
    draw.rectangle(box(fitz.Rect(ix1, iy0, ox1f, iy1)), fill=dark)

    composed = Image.alpha_composite(base, layer).convert('RGB')
    buf = BytesIO()
    composed.save(buf, format='JPEG', quality=94, optimize=True)

    page.add_redact_annot(page.rect, fill=(1, 1, 1))
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)
    page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)


def _overlay_plantilla_page(page, employee) -> dict:
    """Plantilla layout: photo; QR (employee ID) left; name / dept / ID NO on the right."""
    status = {
        'has_photo': False,
        'has_qr': False,
        'name': format_plantilla_id_name(employee),
        'department': format_id_card_department(employee),
        'employee_id': (employee.employee_id or '').strip(),
        'format': 'Plantilla',
    }

    fitted = None
    data, _mime, _rec = read_employee_id_photo(employee)
    if data:
        try:
            fitted = _photo_bytes_for_id_slot(data, PLANTILLA_PHOTO_W, PLANTILLA_PHOTO_H)
            status['has_photo'] = True
        except Exception:
            fitted = None
            status['has_photo'] = False

    _apply_plantilla_photo_and_frame(page, fitted)

    archivo = _archivo_black_fontfile()
    if archivo:
        page.insert_font(fontname='archivo_black', fontfile=archivo)

    arial = _arial_fontfile(bold=False)
    arial_bold = _arial_fontfile(bold=True)
    if arial:
        page.insert_font(fontname='arial', fontfile=arial)
    if arial_bold:
        page.insert_font(fontname='arial_bold', fontfile=arial_bold)

    status['has_qr'] = _draw_qr_identity_block(
        page,
        qr_rect=PLANTILLA_QR_RECT,
        name_rect=PLANTILLA_NAME_RECT,
        dept_rect=PLANTILLA_DEPT_RECT,
        id_box=PLANTILLA_ID_BOX,
        name=status['name'],
        department=status['department'],
        employee_id=status['employee_id'],
        name_font='archivo_black' if archivo else 'hebo',
        name_fontfile=archivo,
        dept_font='arial' if arial else 'helv',
        id_font='arial_bold' if arial_bold else 'hebo',
        name_max_size=PLANTILLA_NAME_MAX_SIZE,
        name_min_size=PLANTILLA_NAME_MIN_SIZE,
        name_tracking=PLANTILLA_NAME_TRACKING,
        id_max_size=PLANTILLA_ID_MAX_SIZE,
        id_min_size=PLANTILLA_ID_MIN_SIZE,
        arial_bold_path=arial_bold,
    )
    return status


def _overlay_employee_on_page(page, employee) -> dict:
    if is_jo_cos_employee(employee):
        return _overlay_jo_cos_page(page, employee)
    return _overlay_plantilla_page(page, employee)


# JO/COS print sheet: four 4" × 6" slots on A4 (2×2). Uniformly scaled to fit.
_INCH_PT = 72.0
JO_COS_PRINT_CELL_W = 4.0 * _INCH_PT  # 288 pt
JO_COS_PRINT_CELL_H = 6.0 * _INCH_PT  # 432 pt
A4_PAGE_RECT = fitz.paper_rect('a4')


def _jo_cos_a4_slot_rects() -> tuple[fitz.Rect, fitz.Rect, fitz.Rect, fitz.Rect]:
    """Top-left, top-right, bottom-left, bottom-right cells (~4\"×6\") centered on A4."""
    page = A4_PAGE_RECT
    scale = min(
        page.width / (2.0 * JO_COS_PRINT_CELL_W),
        page.height / (2.0 * JO_COS_PRINT_CELL_H),
    )
    cw = JO_COS_PRINT_CELL_W * scale
    ch = JO_COS_PRINT_CELL_H * scale
    ox = (page.width - 2.0 * cw) / 2.0
    oy = (page.height - 2.0 * ch) / 2.0
    tl = fitz.Rect(ox, oy, ox + cw, oy + ch)
    tr = fitz.Rect(ox + cw, oy, ox + 2.0 * cw, oy + ch)
    bl = fitz.Rect(ox, oy + ch, ox + cw, oy + 2.0 * ch)
    br = fitz.Rect(ox + cw, oy + ch, ox + 2.0 * cw, oy + 2.0 * ch)
    return tl, tr, bl, br


def _open_filled_id_card_doc(employee) -> fitz.Document:
    """Open template, overlay employee data; caller must close the document."""
    template = id_template_path_for_employee(employee)
    if not template.is_file():
        raise FileNotFoundError(f'ID template not found: {template}')
    doc = fitz.open(template)
    if doc.page_count < 1:
        doc.close()
        raise ValueError('ID template has no pages.')
    _overlay_employee_on_page(doc[0], employee)
    return doc


def _place_id_card_in_slot(dest_page, src_doc: fitz.Document, slot: fitz.Rect) -> None:
    """Draw source card page into slot, aspect-fit and centered within the cell."""
    src_rect = src_doc[0].rect
    if src_rect.width <= 0 or src_rect.height <= 0 or slot.width <= 0 or slot.height <= 0:
        return
    scale = min(slot.width / src_rect.width, slot.height / src_rect.height)
    tw = src_rect.width * scale
    th = src_rect.height * scale
    x0 = slot.x0 + (slot.width - tw) / 2.0
    y0 = slot.y0 + (slot.height - th) / 2.0
    dest_page.show_pdf_page(fitz.Rect(x0, y0, x0 + tw, y0 + th), src_doc, 0)


def _append_jo_cos_a4_sheet(
    out: fitz.Document,
    top_employee,
    bottom_employee=None,
) -> None:
    """
    One A4 sheet for JO/COS:
    - top half: two copies of top_employee
    - bottom half: two copies of bottom_employee (optional; blank if None)
    """
    page = out.new_page(width=A4_PAGE_RECT.width, height=A4_PAGE_RECT.height)
    tl, tr, bl, br = _jo_cos_a4_slot_rects()
    top_doc = _open_filled_id_card_doc(top_employee)
    try:
        _place_id_card_in_slot(page, top_doc, tl)
        _place_id_card_in_slot(page, top_doc, tr)
    finally:
        top_doc.close()
    if bottom_employee is None:
        return
    bot_doc = _open_filled_id_card_doc(bottom_employee)
    try:
        _place_id_card_in_slot(page, bot_doc, bl)
        _place_id_card_in_slot(page, bot_doc, br)
    finally:
        bot_doc.close()


def _append_plantilla_card_page(out: fitz.Document, employee) -> None:
    """Plantilla stays one card page (template size) per employee."""
    src = _open_filled_id_card_doc(employee)
    try:
        out.insert_pdf(src)
    finally:
        src.close()


def build_employee_id_card_pdf(employee) -> bytes:
    """
    Printable ID PDF for one employee.
    JO/COS: A4 with two copies on the top half (bottom half blank).
    Plantilla: single template-size card page.
    """
    if is_jo_cos_employee(employee):
        out = fitz.open()
        try:
            _append_jo_cos_a4_sheet(out, employee, None)
            return out.tobytes(deflate=True, garbage=3)
        finally:
            out.close()

    doc = _open_filled_id_card_doc(employee)
    try:
        return doc.tobytes(deflate=True, garbage=3)
    finally:
        doc.close()


def build_employee_id_cards_batch_pdf(employees: list) -> bytes:
    """
    Multi-page batch PDF.
    JO/COS: A4 sheets with two employees per page when possible
    (top half = 2× employee A, bottom half = 2× employee B).
    Odd last JO/COS uses top half only. Plantilla stays one card page each.
    Mixed lists keep selection order; adjacent JO/COS pair on one sheet.
    """
    if not employees:
        raise ValueError('No employees selected for ID card printing.')

    out = fitz.open()
    try:
        i = 0
        n = len(employees)
        while i < n:
            emp = employees[i]
            if is_jo_cos_employee(emp):
                nxt = employees[i + 1] if (i + 1) < n else None
                if nxt is not None and is_jo_cos_employee(nxt):
                    _append_jo_cos_a4_sheet(out, emp, nxt)
                    i += 2
                else:
                    _append_jo_cos_a4_sheet(out, emp, None)
                    i += 1
            else:
                _append_plantilla_card_page(out, emp)
                i += 1
        return out.tobytes(deflate=True, garbage=3)
    finally:
        out.close()


def id_card_download_name(employee) -> str:
    badge = (employee.employee_id or 'employee').strip().replace(' ', '_')
    kind = 'JOCOS' if is_jo_cos_employee(employee) else 'Plantilla'
    return f'LGU_Jagna_ID_{kind}_{badge}.pdf'

"""匯出 PPT：封面 + 一則紀錄一頁（依分類、再依 tag 排序），之後可以丟給 AI 美化。

有 config/ppt_template.pptx 時會沿用它的投影片大小與主題（使用其中的空白版面配置），
沒有就用內建的 16:9 樣式。
"""
import math
from io import BytesIO
from pathlib import Path

from PIL import Image as PILImage, ImageOps
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from .db import REPORT_ORDER, STATUS_ICONS, STATUS_LABELS, Database, Entry, same_level_key, span_text
from .report import NumberedImage, continued_since, format_entry_line, number_images, report_folder

FONT = "Microsoft JhengHei"
STATUS_RGB = {"todo": RGBColor(0x6B, 0x75, 0x80), "doing": RGBColor(0x2F, 0x6F, 0xD6),
              "waiting": RGBColor(0xE0, 0x8A, 0x00), "review": RGBColor(0x8A, 0x4F, 0xD1),
              "done": RGBColor(0x2E, 0x9D, 0x5B), "cancelled": RGBColor(0xA0, 0xA0, 0xA0)}
DARK = RGBColor(0x22, 0x22, 0x22)
MUTED = RGBColor(0x80, 0x80, 0x80)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
CATEGORY_RGB = RGBColor(0x5F, 0x63, 0x68)
PRIORITY_RGB = {1: RGBColor(0xD6, 0x45, 0x45), 2: RGBColor(0xE0, 0x8A, 0x00)}


def order_by_category(items: list[tuple[Entry, list]], category_order: list[int]) -> list[tuple[Entry, list]]:
    """依分類樹的順序（category_order 是樹狀順序的分類 id，先父後子），未分類放最後；
    同一個分類內依優先順序、再依標題。tag 不影響順序。"""
    rank = {cid: i for i, cid in enumerate(category_order)}
    return sorted(items, key=lambda it: (rank.get(it[0].category_id, len(rank)), same_level_key(it[0])))


# ---- 繪圖小工具 ----

def _style_run(run, size, bold=False, color=DARK):
    font = run.font
    font.size = Pt(size)
    font.bold = bold
    font.color.rgb = color
    font.name = FONT
    # 中文字型要另外指定 East Asian typeface，否則 PowerPoint 會用主題預設字型
    rpr = run._r.get_or_add_rPr()
    ea = rpr.find(qn("a:ea"))
    if ea is None:
        ea = rpr.makeelement(qn("a:ea"), {})
        rpr.append(ea)
    ea.set("typeface", FONT)


def _text(slide, left, top, width, height, text, size, *, bold=False, color=DARK, align=None,
          anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for i, line in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        if align is not None:
            p.alignment = align
        run = p.add_run()
        run.text = line
        _style_run(run, size, bold, color)
    return box


def _rect(slide, left, top, width, height, color, shape=MSO_SHAPE.RECTANGLE):
    rect = slide.shapes.add_shape(shape, left, top, width, height)
    rect.fill.solid()
    rect.fill.fore_color.rgb = color
    rect.line.fill.background()
    rect.shadow.inherit = False
    return rect


def _chip(slide, left, top, text, color):
    width = Inches(0.35 + 0.22 * len(text))
    chip = _rect(slide, left, top, width, Inches(0.42), color, MSO_SHAPE.ROUNDED_RECTANGLE)
    tf = chip.text_frame
    tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = text
    _style_run(run, 14, True, WHITE)
    return width


def _image_stream(src: Path, max_side: int) -> tuple[BytesIO, int, int]:
    with PILImage.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), PILImage.LANCZOS)
        buf = BytesIO()
        if src.suffix.lower() in (".jpg", ".jpeg"):
            im.convert("RGB").save(buf, "JPEG", quality=90)
        else:
            im.save(buf, "PNG")
        buf.seek(0)
        return buf, im.width, im.height


def _blank_layout(prs):
    for layout in prs.slide_layouts:
        if layout.name.lower() in ("blank", "空白"):
            return layout
    return prs.slide_layouts[len(prs.slide_layouts) - 1]


# ---- 投影片 ----

def _cover(prs, date: str, entries: list[Entry]):
    slide = prs.slides.add_slide(_blank_layout(prs))
    w, h = prs.slide_width, prs.slide_height
    _rect(slide, 0, 0, w, Inches(0.25), STATUS_RGB["doing"])
    _text(slide, Inches(0.8), Inches(2.0), w - Inches(1.6), Inches(1.2), "工作回報", 48, bold=True)
    _text(slide, Inches(0.8), Inches(3.1), w - Inches(1.6), Inches(0.6), date, 24, color=MUTED)

    left = Inches(0.8)
    for status in sorted(STATUS_LABELS, key=REPORT_ORDER.get):
        count = sum(e.status == status for e in entries)
        if not count:
            continue
        left += _chip(slide, left, Inches(4.1), f"{STATUS_LABELS[status]} {count}", STATUS_RGB[status]) + Inches(0.2)

    categories = []
    for e in entries:
        if e.category and e.category not in categories:
            categories.append(e.category)
    if categories:
        _text(slide, Inches(0.8), Inches(4.9), w - Inches(1.6), Inches(1.0), "　".join(categories), 18, color=MUTED)


def _entry_slide(prs, entry: Entry, images: list[NumberedImage], date: str, page: int, total: int,
                 max_side: int, since: str | None):
    slide = prs.slides.add_slide(_blank_layout(prs))
    w, h = prs.slide_width, prs.slide_height
    color = STATUS_RGB[entry.status]
    margin = Inches(0.6)
    shown = [img for img in images if img.available]

    _rect(slide, 0, 0, Inches(0.18), h, color)

    # 第一行：時段、狀態、分類、tag
    _text(slide, margin, Inches(0.35), Inches(1.6), Inches(0.45), span_text(entry, duration=False), 16,
          color=MUTED, anchor=MSO_ANCHOR.MIDDLE)
    x = margin + Inches(1.6)
    x += _chip(slide, x, Inches(0.37), f"{STATUS_ICONS[entry.status]} {STATUS_LABELS[entry.status]}", color)
    x += Inches(0.15) + _chip(slide, x + Inches(0.15), Inches(0.37), f"優先 {entry.priority}",
                              PRIORITY_RGB.get(entry.priority, CATEGORY_RGB))
    if entry.category:
        x += Inches(0.15) + _chip(slide, x + Inches(0.15), Inches(0.37), entry.category, CATEGORY_RGB)
    tags = " ".join(f"#{t}" for t in entry.tags)
    if since:
        tags += f"　（延續自 {since[5:]}）"
    if tags:
        _text(slide, x + Inches(0.2), Inches(0.35), w - x - margin, Inches(0.45), tags.strip(), 16, color=color,
              bold=True, anchor=MSO_ANCHOR.MIDDLE)

    # 內容：有圖時文字放左邊、圖片放右邊
    text_w = (w - margin * 2) * (0.45 if shown else 1.0)
    lines = entry.text.strip().replace("\r\n", "\n").split("\n")
    if entry.title:  # 有標題時標題當大字，內容全部列在下面
        lines.insert(0, entry.title)
    _text(slide, margin, Inches(1.1), text_w, Inches(1.2), lines[0], 28, bold=True)
    body = [f"• {line.strip()}" for line in lines[1:] if line.strip()]
    body += [f"（{img.ref}）" for img in images if not img.available]
    if body:
        _text(slide, margin, Inches(2.4), text_w, h - Inches(3.2), "\n".join(body), 18)

    if shown:
        _place_images(slide, shown, margin + text_w + Inches(0.4), Inches(1.1),
                      w - margin * 2 - text_w - Inches(0.4), h - Inches(1.9), max_side)

    _text(slide, w - Inches(3.6), h - Inches(0.6), Inches(3.0), Inches(0.4), f"{date}　{page} / {total}", 11,
          color=MUTED, align=PP_ALIGN.RIGHT)
    # 備忘稿放原始紀錄，AI 美化時可以參考
    slide.notes_slide.notes_text_frame.text = format_entry_line(entry, [img.ref for img in images], since)


def _place_images(slide, images: list[NumberedImage], left, top, width, height, max_side):
    cols = 1 if len(images) == 1 else 2
    rows = math.ceil(len(images) / cols)
    gap, caption = Inches(0.2), Inches(0.35)
    cell_w = (width - gap * (cols - 1)) / cols
    cell_h = (height - gap * (rows - 1)) / rows
    for i, img in enumerate(images):
        stream, iw, ih = _image_stream(img.src, max_side)
        scale = min(cell_w / iw, (cell_h - caption) / ih)
        pw, ph = int(iw * scale), int(ih * scale)
        cx = left + (i % cols) * (cell_w + gap)
        cy = top + (i // cols) * (cell_h + gap)
        pic = slide.shapes.add_picture(stream, Emu(int(cx + (cell_w - pw) / 2)), Emu(int(cy)), Emu(pw), Emu(ph))
        pic.line.color.rgb = RGBColor(0xDD, 0xDD, 0xDD)
        _text(slide, Emu(int(cx)), Emu(int(cy + ph)), Emu(int(cell_w)), caption, f"附圖{img.number}", 12,
              color=MUTED, align=PP_ALIGN.CENTER)


def export_slides(db: Database, date: str, output_root, template: Path | None = None,
                  max_side: int = 1600) -> Path:
    entries = db.entries_for_date(date)
    if template is not None and template.exists():
        prs = Presentation(str(template))
    else:
        prs = Presentation()
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    items = order_by_category(number_images(db, entries), [cid for cid, _label in db.categories()])
    _cover(prs, date, [entry for entry, _images in items])
    for page, (entry, images) in enumerate(items, start=1):
        _entry_slide(prs, entry, images, date, page, len(items), max_side, continued_since(db, entry))

    path = report_folder(output_root, date) / "report.pptx"
    prs.save(path)
    return path

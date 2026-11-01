"""工作回顧：把某天的紀錄整理成 report_YYYY-MM-DD/ 資料夾。"""
import re
from pathlib import Path

from PIL import Image as PILImage, ImageOps

from .db import STATUS_LABELS, Database, Entry

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\s\x00-\x1f]+')
_GENERATED_FILE = re.compile(r"^\d{2,}_.+\.(png|jpg)$", re.IGNORECASE)


def slugify(text: str, max_len: int = 20) -> str:
    """取紀錄第一行當作檔名描述，移除空白與 Windows 檔名不允許的字元。"""
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    slug = _INVALID_CHARS.sub("", first_line).strip(". ")
    return slug[:max_len] or "image"


def output_suffix(src: Path) -> str:
    return ".jpg" if src.suffix.lower() in (".jpg", ".jpeg") else ".png"


def resize_image(src: Path, dst: Path, max_side: int = 1600):
    """複製圖片並把長邊縮到 max_side（較小的圖不放大）。"""
    with PILImage.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), PILImage.LANCZOS)
        if dst.suffix.lower() == ".jpg":
            im.convert("RGB").save(dst, quality=90)
        else:
            im.save(dst)


def format_entry_line(entry: Entry, image_refs: list[str]) -> str:
    head = f"[{entry.time}][{STATUS_LABELS[entry.status]}]"
    if entry.tags:
        head += "[" + " ".join(f"#{t}" for t in entry.tags) + "]"
    lines = entry.text.strip().replace("\r\n", "\n").split("\n")
    body = lines[0] + "".join(f"\n  {line}" for line in lines[1:])
    if image_refs:
        body += "（" + "、".join(image_refs) + "）"
    return f"- {head} {body}"


def build_markdown(date: str, instructions: str, entry_lines: list[str]) -> str:
    records = "\n".join(entry_lines) if entry_lines else "（今天沒有紀錄）"
    return (
        f"# 工作回報整理請求（{date}）\n\n"
        f"## 指示\n{instructions.strip()}\n\n"
        f"## 今日工作紀錄\n{records}\n"
    )


def _clear_previous_output(folder: Path):
    """重新產生時只清掉本工具產生的檔案，資料夾裡其他東西不動。"""
    for f in folder.iterdir():
        if f.is_file() and (f.name == "report.md" or _GENERATED_FILE.match(f.name)):
            f.unlink()


def generate_report(db: Database, date: str, output_root, instructions: str, max_side: int = 1600) -> Path:
    folder = Path(output_root) / f"report_{date}"
    folder.mkdir(parents=True, exist_ok=True)
    _clear_previous_output(folder)

    n = 0
    lines = []
    for entry in db.entries_for_date(date):
        refs = []
        for img in entry.images:
            n += 1
            if not img.include:
                refs.append(f"附圖{n}：未提供")
                continue
            src = db.abs_path(img.path)
            if not src.exists():
                refs.append(f"附圖{n}：檔案遺失")
                continue
            name = f"{n:02d}_{slugify(entry.text)}{output_suffix(src)}"
            resize_image(src, folder / name, max_side)
            refs.append(f"附圖{n}：{name}")
        lines.append(format_entry_line(entry, refs))

    (folder / "report.md").write_text(build_markdown(date, instructions, lines), encoding="utf-8")
    return folder

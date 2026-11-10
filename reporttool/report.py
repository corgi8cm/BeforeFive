"""工作回顧：把某天的紀錄整理成 report_YYYY-MM-DD/ 資料夾。"""
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image as PILImage, ImageOps

from .changes import EntryChange, change_for, format_change_lines
from .db import DEFAULT_PRIORITY, STATUS_LABELS, Database, Entry, hours_text, span_text

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\s\x00-\x1f]+')
_GENERATED_FILE = re.compile(r"^\d{2,}_.+\.(png|jpg)$", re.IGNORECASE)


def slugify(text: str, max_len: int = 20) -> str:
    """取第一行當作檔名描述，移除空白與 Windows 檔名不允許的字元。"""
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


def continued_since(db: Database, entry: Entry) -> str | None:
    """這件工作最早一筆紀錄的日期；不是延續的工作回傳 None。"""
    history = db.history(entry.id)
    return history[0].date if history else None


def report_span(entry: Entry) -> str:
    """「09:00–10:30 1.5h」；時長 0 只寫一個時間。"""
    if entry.minutes == 0:
        return entry.start or entry.time
    return f"{span_text(entry, duration=False)} {hours_text(entry.minutes)}"


def format_entry_line(entry: Entry, image_refs: list[str], since: str | None = None,
                      change: EntryChange | None = None) -> str:
    head = f"[{report_span(entry)}][{STATUS_LABELS[entry.status]}][優先{entry.priority}]"
    if entry.category:
        head += f"[分類：{entry.category}]"
    if entry.tags:
        head += "[" + " ".join(f"#{t}" for t in entry.tags) + "]"
    lines = entry.text.strip().replace("\r\n", "\n").split("\n")
    body = lines[0] + "".join(f"\n  {line}" for line in lines[1:])
    if entry.title:
        body = f"【{entry.title}】{body}"
    if since:
        body += f"（延續自 {since[5:]}）"
    if image_refs:
        body += "（" + "、".join(image_refs) + "）"
    if change:
        body += format_change_lines(change)
    return f"- {head} {body}"


def image_definitions(definitions: list[str]) -> str:
    """放在檔案最後的圖片引用定義，前面空一行。"""
    return "\n" + "\n".join(definitions) + "\n" if definitions else ""


def category_info_lines(db: Database, entries: list[Entry]) -> list[str]:
    """紀錄用到的分類（含上層），依分類樹的順序（同層已依優先度排好）。

    有設定的才寫：優先度不是預設 3 時寫「優先N」，有進度時寫「進度 N%」，例如「- 開發 › 專案A：優先1、進度 40%」。"""
    nodes = db.category_tree()
    parents = {n.id: n.parent_id for n in nodes}
    used = set()
    for e in entries:
        cid = e.category_id
        while cid is not None and cid not in used:
            used.add(cid)
            cid = parents.get(cid)
    lines = []
    for n in nodes:
        if n.id not in used:
            continue
        info = ([f"優先{n.priority}"] if n.priority != DEFAULT_PRIORITY else []) + \
               ([f"進度 {n.progress}%"] if n.progress is not None else [])
        lines.append(f"- {n.label}" + ("：" + "、".join(info) if info else ""))
    return lines


def category_section(category_lines: list[str]) -> str:
    return "\n## 分類資訊\n" + "\n".join(category_lines) + "\n" if category_lines else ""


def build_markdown(date: str, instructions: str, entry_lines: list[str], definitions: list[str] = (),
                   category_lines: list[str] = ()) -> str:
    records = "\n".join(entry_lines) if entry_lines else "（今天沒有紀錄）"
    return (
        f"# 工作回報整理請求（{date}）\n\n"
        f"## 指示\n{instructions.strip()}\n\n"
        f"## 今日工作紀錄\n{records}\n"
        + category_section(list(category_lines))
        + image_definitions(list(definitions))
    )


def _clear_previous_output(folder: Path):
    """重新產生時只清掉本工具產生的檔案，資料夾裡其他東西不動。"""
    for f in folder.iterdir():
        if f.is_file() and (f.name == "report.md" or _GENERATED_FILE.match(f.name)):
            f.unlink()


@dataclass
class NumberedImage:
    number: int  # 當天從 1 開始的附圖編號
    src: Path  # 原圖位置
    filename: str  # 給 AI 的檔名，例如 01_修登入頁.png
    include: bool

    @property
    def ref(self) -> str:
        if not self.include:
            return f"附圖{self.number}：未提供"
        if not self.src.exists():
            return f"附圖{self.number}：檔案遺失"
        return f"附圖{self.number}：{self.filename}"

    @property
    def available(self) -> bool:
        return self.include and self.src.exists()

    @property
    def md_ref(self) -> str:
        """report.md 用的寫法：有圖時用引用圖片 ![附圖N]，檔名寫在檔案最後的定義。"""
        return f"![附圖{self.number}]" if self.available else self.ref

    @property
    def definition(self) -> str:
        return f"[附圖{self.number}]: <{self.filename}>"


def number_images(db: Database, entries: list[Entry]) -> list[tuple[Entry, list[NumberedImage]]]:
    """依紀錄時間順序替圖片編號，report.md 與 PPT 共用同一套編號。"""
    n = 0
    result = []
    for entry in entries:
        images = []
        for img in entry.images:
            n += 1
            src = db.abs_path(img.path)
            images.append(NumberedImage(n, src, f"{n:02d}_{slugify(entry.title or entry.text)}{output_suffix(src)}", img.include))
        result.append((entry, images))
    return result


def report_folder(output_root, date: str) -> Path:
    folder = Path(output_root) / f"report_{date}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write_entries(db: Database, entries: list[Entry], folder: Path,
                   max_side: int) -> tuple[list[tuple[Entry, str]], list[str]]:
    """複製附圖到 folder。回傳（每筆紀錄與它在 report.md 中的那一行, 圖片引用定義）。"""
    _clear_previous_output(folder)
    lines, definitions = [], []
    for entry, images in number_images(db, entries):
        for img in images:
            if img.available:
                resize_image(img.src, folder / img.filename, max_side)
                definitions.append(img.definition)
        lines.append((entry, format_entry_line(entry, [img.md_ref for img in images], continued_since(db, entry),
                                               change_for(db, entry))))
    return lines, definitions


def generate_report(db: Database, date: str, output_root, instructions: str, max_side: int = 1600) -> Path:
    folder = report_folder(output_root, date)
    entries = db.entries_for_date(date)
    written, definitions = _write_entries(db, entries, folder, max_side)
    md = build_markdown(date, instructions, [line for _, line in written], definitions,
                        category_info_lines(db, entries))
    (folder / "report.md").write_text(md, encoding="utf-8")
    return folder


WEEKDAYS = "一二三四五六日"


def date_heading(date: str) -> str:
    d = datetime.strptime(date, "%Y-%m-%d")
    return f"### {date}（週{WEEKDAYS[d.weekday()]}）"


def build_range_markdown(start: str, end: str, instructions: str, entry_lines: list[tuple[str, str]],
                         definitions: list[str] = (), category_lines: list[str] = ()) -> str:
    """entry_lines：依時間排序的（日期, 紀錄那一行）。每天一個小標題，沒有紀錄的日子不列出。"""
    days: dict[str, list[str]] = {}
    for date, line in entry_lines:
        days.setdefault(date, []).append(line)
    if days:
        records = "\n\n".join(date_heading(d) + "\n" + "\n".join(lines) for d, lines in days.items())
    else:
        records = "（這段期間沒有紀錄）"
    return (
        f"# 工作回報整理請求（{start} ~ {end}）\n\n"
        f"## 指示\n{instructions.strip()}\n\n"
        f"## 期間工作紀錄（共 {len(entry_lines)} 筆，{len(days)} 天）\n{records}\n"
        + category_section(list(category_lines))
        + image_definitions(list(definitions))
    )


def range_folder(output_root, start: str, end: str) -> Path:
    folder = Path(output_root) / f"report_{start}_to_{end}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def generate_range_report(db: Database, start: str, end: str, output_root, instructions: str,
                          max_side: int = 1600) -> Path:
    """整理 start～end（含頭尾）的所有紀錄，附圖編號跨日連續。"""
    folder = range_folder(output_root, start, end)
    entries = db.entries_between(start, end)
    written, definitions = _write_entries(db, entries, folder, max_side)
    md = build_range_markdown(start, end, instructions, [(e.date, line) for e, line in written], definitions,
                              category_info_lines(db, entries))
    (folder / "report.md").write_text(md, encoding="utf-8")
    return folder

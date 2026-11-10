"""甘特圖要畫的資料（不含畫圖，方便測試）。

- 只選一天（day 模式）：橫軸是時間，每筆紀錄一列，畫成起始到結束的長條；時長 0 畫成一個點。
- 選好幾天（days 模式）：橫軸是日期，每件工作（一條延續鏈，分法同範圍整理）一列，有做的那幾天畫成色塊。
- 兩種都依分類樹分組（工作用範圍內最新那筆的分類），顏色用狀態的顏色。
"""
from dataclasses import dataclass, field
from datetime import date as Date, timedelta

from .db import STATUS_LABELS, Database, Entry, duration_text, span_text
from .report import WEEKDAYS, Work, day_tag, group_works


@dataclass
class Bar:
    start: float  # day 模式：分鐘（0~1440）；days 模式：第幾天（0 起算）
    end: float  # day 模式：分鐘；days 模式：第幾天 + 1
    status: str
    entry_id: int  # 雙擊跳到這筆
    tooltip: str
    point: bool = False  # 時長 0：畫成一個點


@dataclass
class Row:
    label: str
    entry_id: int | None = None  # 雙擊標題跳到這筆；分組標題是 None
    bars: list[Bar] = field(default_factory=list)
    group: bool = False  # 分類的分組標題列
    tooltip: str = ""


@dataclass
class Gantt:
    start: str
    end: str
    rows: list[Row]
    axis_start: float  # day 模式：分鐘；days 模式：0
    axis_end: float  # day 模式：分鐘；days 模式：天數

    @property
    def single_day(self) -> bool:
        return self.start == self.end

    @property
    def days(self) -> list[str]:
        first = Date.fromisoformat(self.start)
        return [(first + timedelta(days=i)).isoformat() for i in range(int(self.axis_end))]

    @property
    def empty(self) -> bool:
        return not any(r.bars for r in self.rows)


def minutes_of(clock: str) -> int:
    h, m = clock.split(":")
    return int(h) * 60 + int(m)


def entry_tooltip(entry: Entry, with_date: bool = False) -> str:
    title = f"【{entry.title}】" if entry.title else ""
    first = entry.text.strip().splitlines()[0] if entry.text.strip() else ""
    span = span_text(entry, duration=False)
    if entry.minutes:
        span += f"（{duration_text(entry.minutes)}）"
    when = f"{day_tag(entry.date)} {span}" if with_date else span
    return f"{title}{first}\n{when}　{STATUS_LABELS[entry.status]}"


def _grouped(db: Database, items: list, category_of) -> list[tuple[str, list]]:
    """依分類樹的順序分組：（分類完整路徑, 這個分類直接擁有的項目），沒有項目的分類不列，未分類放最後。"""
    by_category: dict = {}
    for item in items:
        by_category.setdefault(category_of(item), []).append(item)
    groups = [(n.label, by_category.pop(n.id)) for n in db.category_tree() if n.id in by_category]
    leftovers = [i for items in by_category.values() for i in items]  # 未分類（或分類已刪除）
    if leftovers:
        groups.append(("未分類", leftovers))
    return groups


def _day_rows(db: Database, entries: list[Entry]) -> list[Row]:
    rows = []
    for label, items in _grouped(db, entries, lambda e: e.category_id):
        rows.append(Row(label, group=True))
        for e in sorted(items, key=lambda e: (e.start, e.end, e.time, e.id)):
            start, end = minutes_of(e.start), minutes_of(e.end)
            tip = entry_tooltip(e)
            rows.append(Row(e.summary, e.id, [Bar(start, end, e.status, e.id, tip, point=start == end)], tooltip=tip))
    return rows


def _work_rows(db: Database, start: str, works: list[Work]) -> list[Row]:
    first = Date.fromisoformat(start)
    rows = []
    for label, items in _grouped(db, works, lambda w: w.head.category_id):
        rows.append(Row(label, group=True))
        for work in items:  # 已經依最早那筆排好
            head = work.head
            named = work.entries[0] if len(work.latest) > 1 else head  # 名稱規則同範圍整理
            name = named.title or named.summary
            by_day: dict[str, list[Entry]] = {}
            for e in work.entries:
                by_day.setdefault(e.date, []).append(e)
            bars = []
            for day, day_entries in by_day.items():
                i = (Date.fromisoformat(day) - first).days
                last = day_entries[-1]  # 這天最晚的那筆：顏色用它的狀態，雙擊跳到它
                minutes = sum(e.minutes for e in day_entries)
                total = f"（共 {duration_text(minutes)}）" if minutes and len(day_entries) > 1 else ""
                tip = f"{name}\n{day_tag(day)}{total}\n" + "\n".join(
                    "　" + entry_tooltip(e).replace("\n", "　") for e in day_entries)
                bars.append(Bar(i, i + 1, last.status, last.id, tip))
            total = sum(e.minutes for e in work.entries)
            tip = f"{name}\n範圍內 {len(work.entries)} 筆、{len(by_day)} 天" + (
                f"，合計 {duration_text(total)}" if total else "")
            rows.append(Row(name, head.id, bars, tooltip=tip))
    return rows


def build_gantt(db: Database, start: str, end: str) -> Gantt:
    entries = db.entries_between(start, end)
    if start == end:
        rows = _day_rows(db, entries)
        if entries:  # 前後留到整點，至少 09:00~18:00
            lo = min(minutes_of(e.start) for e in entries)
            hi = max(minutes_of(e.end) for e in entries)
            axis_start = min(lo // 60 * 60, 9 * 60)
            axis_end = max(-(-hi // 60) * 60, 18 * 60)
            if axis_end == axis_start:
                axis_end += 60
        else:
            axis_start, axis_end = 9 * 60, 18 * 60
        return Gantt(start, end, rows, axis_start, min(axis_end, 24 * 60))
    days = (Date.fromisoformat(end) - Date.fromisoformat(start)).days + 1
    return Gantt(start, end, _work_rows(db, start, group_works(db, entries)), 0, days)


def weekday(day: str) -> str:
    return WEEKDAYS[Date.fromisoformat(day).weekday()]

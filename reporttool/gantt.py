"""甘特圖要畫的資料（不含畫圖，方便測試）。

- 只選一天（day 模式）：橫軸是時間，每筆紀錄一列，畫成起始到結束的長條；時長 0 畫成一個點。
  時段跨到別天的只畫這天的部分；時段完全不在這天的（例如帶入的紀錄沿用前一天的時段）只寫出時段、不畫長條。
- 選好幾天（days 模式）：每件工作（一條延續鏈，分法同範圍整理）一列。橫軸是幾天接起來的時間，
  每天都只畫同一個時間窗（至少 09:00~18:00，有更早或更晚的時段就加寬到整點），長條依實際時間畫；
  時段跨過時間窗外（例如跨夜）時，每天各畫落在時間窗內的那段。同一件工作起訖相同的時段只畫一次。
- 兩種都依分類樹分組（工作用範圍內最新那筆的分類），顏色用狀態的顏色。
- 列哪些紀錄看紀錄的日期（和範圍整理相同），畫在哪裡看時段。
"""
from dataclasses import dataclass, field
from datetime import date as Date, timedelta

from .db import STATUS_LABELS, Database, Entry, duration_text, span_text
from .report import WEEKDAYS, Work, day_tag, group_works, unique_minutes

DAY_MINUTES = 24 * 60
DEFAULT_WINDOW = (9 * 60, 18 * 60)


@dataclass
class Bar:
    start: float  # 橫軸上的位置（分鐘）：day 模式是當天幾點；days 模式是 第幾天 × 時間窗長度 ＋ 時間窗內的分鐘
    end: float
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
    note: str = ""  # 沒有長條時寫在圖上的說明（例如時段在別天）


@dataclass
class Gantt:
    start: str
    end: str
    rows: list[Row]
    window: tuple[int, int]  # 每天畫出來的時間窗（當天幾點，分鐘）
    days: list[str]  # 畫出來的每一天，day 模式只有一天

    @property
    def single_day(self) -> bool:
        return self.start == self.end

    @property
    def day_length(self) -> int:
        """時間窗的長度（分鐘）＝橫軸上一天的長度。"""
        return self.window[1] - self.window[0]

    @property
    def axis_start(self) -> float:
        return self.window[0] if self.single_day else 0

    @property
    def axis_end(self) -> float:
        return self.window[1] if self.single_day else len(self.days) * self.day_length

    def position(self, day_index: int, minute: int) -> float:
        """第 day_index 天的 minute（當天幾點）在橫軸上的位置。"""
        if self.single_day:
            return minute
        return day_index * self.day_length + minute - self.window[0]

    @property
    def empty(self) -> bool:
        return not any(r.entry_id is not None for r in self.rows)


def minutes_of(clock: str) -> int:
    h, m = clock.split(":")
    return int(h) * 60 + int(m)


def offset(day: str, span_day: str, clock: str) -> int:
    """span_day 的 clock 是 day 00:00 之後的第幾分鐘（可以是負的或超過一天）。"""
    return (Date.fromisoformat(span_day) - Date.fromisoformat(day)).days * DAY_MINUTES + minutes_of(clock)


def day_part(entry: Entry, day: str) -> tuple[int, int] | None:
    """時段落在 day 的部分（分鐘，0~1440）；完全不在這天時 None。時長 0 的落在那個時間點的那天。"""
    start_date, start, end_date, end = entry.span_key
    lo, hi = offset(day, start_date, start), offset(day, end_date, end)
    if lo == hi:
        return (lo, hi) if 0 <= lo < DAY_MINUTES else None
    if hi <= 0 or lo >= DAY_MINUTES:
        return None
    return max(lo, 0), min(hi, DAY_MINUTES)


def fit_window(parts) -> tuple[int, int]:
    """涵蓋所有 (lo, hi) 的時間窗：前後到整點，至少 09:00~18:00。"""
    parts = list(parts)
    if not parts:
        return DEFAULT_WINDOW
    lo = min(p[0] for p in parts)
    hi = max(p[1] for p in parts)
    start = min(lo // 60 * 60, DEFAULT_WINDOW[0])
    end = min(max(-(-hi // 60) * 60, DEFAULT_WINDOW[1]), DAY_MINUTES)
    return start, end


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
        for e in sorted(items, key=lambda e: (e.span_key, e.time, e.id)):
            tip = entry_tooltip(e)
            part = day_part(e, e.date)
            if part is None:
                rows.append(Row(e.summary, e.id, tooltip=tip, note=f"時段在 {span_text(e, duration=False)}"))
                continue
            start, end = part
            rows.append(Row(e.summary, e.id, [Bar(start, end, e.status, e.id, tip, point=start == end)], tooltip=tip))
    return rows


def _unique_spans(entries: list[Entry]) -> list[Entry]:
    """起訖相同的只留一筆（最晚那筆：顏色用它的狀態，雙擊跳到它），依時段排列。"""
    return sorted({e.span_key: e for e in entries}.values(), key=lambda e: e.span_key)


def _work_rows(db: Database, gantt: Gantt, works: list[Work]) -> list[Row]:
    w0, w1 = gantt.window
    rows = []
    for label, items in _grouped(db, works, lambda w: w.head.category_id):
        rows.append(Row(label, group=True))
        for work in items:  # 已經依最早那筆排好
            head = work.head
            named = work.entries[0] if len(work.latest) > 1 else head  # 名稱規則同範圍整理
            name = named.title or named.summary
            bars, drawn_days = [], set()
            for e in _unique_spans(work.entries):
                tip = f"{name}\n" + entry_tooltip(e, with_date=True)
                for i, day in enumerate(gantt.days):
                    part = day_part(e, day)
                    if part is None:
                        continue
                    lo, hi = max(part[0], w0), min(part[1], w1)  # 時間窗已經涵蓋所有時段，保險起見再截一次
                    if lo > hi or (lo == hi and part[0] != part[1]):
                        continue
                    drawn_days.add(day)
                    bars.append(Bar(gantt.position(i, lo), gantt.position(i, hi), e.status, e.id, tip,
                                    point=lo == hi))
            total = unique_minutes(work.entries)
            tip = f"{name}\n範圍內 {len(work.entries)} 筆、{len(drawn_days)} 天" + (
                f"，合計 {duration_text(total)}" if total else "")
            rows.append(Row(name, head.id, bars, tooltip=tip))
    return rows


def build_gantt(db: Database, start: str, end: str) -> Gantt:
    entries = db.entries_between(start, end)
    if start == end:
        rows = _day_rows(db, entries)
        window = fit_window((b.start, b.end) for r in rows for b in r.bars)
        return Gantt(start, end, rows, window, [start])
    n = (Date.fromisoformat(end) - Date.fromisoformat(start)).days + 1
    days = [(Date.fromisoformat(start) + timedelta(days=i)).isoformat() for i in range(n)]
    window = fit_window(part for e in entries for day in days if (part := day_part(e, day)) is not None)
    gantt = Gantt(start, end, [], window, days)
    gantt.rows = _work_rows(db, gantt, group_works(db, entries))
    return gantt


def weekday(day: str) -> str:
    return WEEKDAYS[Date.fromisoformat(day).weekday()]

"""延續的工作和前一次紀錄（carried_from 那一筆）比較，看出這次改了什麼。"""
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .db import STATUS_LABELS, Database, Entry


def _lines(text: str) -> list[str]:
    return [line for line in text.strip().replace("\r\n", "\n").split("\n") if line.strip()]


@dataclass
class EntryChange:
    entry: Entry
    previous: Entry
    fields: list[str] = field(default_factory=list)  # 例如「狀態 待辦→進行中」

    @property
    def text_changed(self) -> bool:
        return _lines(self.entry.text) != _lines(self.previous.text)

    @property
    def new_images(self) -> int:
        # 帶入時不複製圖片，追加進度是全新的一筆，所以這筆的圖片都是這次新加的
        return len(self.entry.images)

    @property
    def changed(self) -> bool:
        return bool(self.fields or self.text_changed or self.new_images)

    @property
    def previous_label(self) -> str:
        """「11-03」；同一天追加的進度再加上時間。"""
        label = self.previous.date[5:]
        return f"{label} {self.previous.time}" if self.previous.date == self.entry.date else label

    def summary(self) -> str:
        parts = list(self.fields)
        if self.text_changed:
            parts.append("內容有更新")
        if self.new_images:
            parts.append(f"新增附圖 {self.new_images} 張")
        return "；".join(parts) if parts else "沒有變更"

    def text_diff(self) -> list[tuple[str, str]]:
        """逐行比較內容，回傳 [("前"|"後", 那一行)]，相同的行不列出。"""
        old, new = _lines(self.previous.text), _lines(self.entry.text)
        result = []
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag != "equal":
                result += [("前", line) for line in old[i1:i2]] + [("後", line) for line in new[j1:j2]]
        return result


def _field_changes(prev: Entry, cur: Entry) -> list[str]:
    changes = []
    if prev.status != cur.status:
        changes.append(f"狀態 {STATUS_LABELS[prev.status]}→{STATUS_LABELS[cur.status]}")
    if prev.priority != cur.priority:
        changes.append(f"優先 {prev.priority}→{cur.priority}")
    if prev.title != cur.title:
        changes.append(f"標題 {prev.title or '（無）'}→{cur.title or '（無）'}")
    if prev.category != cur.category:
        changes.append(f"分類 {prev.category or '（未分類）'}→{cur.category or '（未分類）'}")
    added = [t for t in cur.tags if t not in prev.tags]
    removed = [t for t in prev.tags if t not in cur.tags]
    if added or removed:
        changes.append("tag " + " ".join([f"+#{t}" for t in added] + [f"-#{t}" for t in removed]))
    return changes


def compare(prev: Entry, cur: Entry) -> EntryChange:
    return EntryChange(cur, prev, _field_changes(prev, cur))


def change_for(db: Database, entry: Entry) -> EntryChange | None:
    """不是延續的工作，或前一筆已被刪除時回傳 None。"""
    if not entry.carried_from:
        return None
    prev = db.get_entry(entry.carried_from)
    return compare(prev, entry) if prev else None


def changes_for(db: Database, entries: list[Entry]) -> list[EntryChange]:
    return [c for c in (change_for(db, e) for e in entries) if c]


def format_change_lines(change: EntryChange) -> str:
    """report.md 中接在紀錄後面的幾行（含開頭換行）。"""
    text = f"\n  ⇄ 與 {change.previous_label} 相比：{change.summary()}"
    return text + "".join(f"\n    {side}：{line}" for side, line in change.text_diff())


def char_segments(old: str, new: str) -> tuple[list[tuple[str, bool]], list[tuple[str, bool]]]:
    """逐字比較，回傳（舊文字片段, 新文字片段），每段是（文字, 是否有變）。比較視窗用來上色。"""
    old_parts, new_parts = [], []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if i2 > i1:
            old_parts.append((old[i1:i2], tag != "equal"))
        if j2 > j1:
            new_parts.append((new[j1:j2], tag != "equal"))
    return old_parts, new_parts

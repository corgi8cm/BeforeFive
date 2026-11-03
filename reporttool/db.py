"""SQLite 資料層：紀錄、標籤、分類、圖片。

圖片原檔存放在 data_dir/images/YYYY-MM-DD/，資料庫只記相對路徑。

為了之後和手機同步（見 docs/data-format.md）：
- 每筆紀錄有 uuid；跨裝置用 uuid 辨識，本機的整數 id 只在這台電腦有意義
- updated_at 一律是 UTC 毫秒字串（YYYY-MM-DDTHH:MM:SS.mmmZ），同步時以較新的為準
- 刪除紀錄與分類只標記 deleted_at（tombstone），讓另一台裝置知道它被刪了
- 分類和 tag 一樣用名稱辨識，兩台裝置各自建立同名分類時會自然合併
"""
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("done", "doing", "blocked")
STATUS_LABELS = {"done": "完成", "doing": "進行中", "blocked": "卡住"}
UNFINISHED = ("doing", "blocked")
DEFAULT_CATEGORIES = ("開發", "會議", "支援", "行政", "其他")
PRIORITIES = (1, 2, 3, 4, 5)  # 1 最優先
DEFAULT_PRIORITY = 3
SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    carried_from INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date);
CREATE TABLE IF NOT EXISTS entry_tags (
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (entry_id, tag)
);
CREATE TABLE IF NOT EXISTS tags (
    name TEXT PRIMARY KEY,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    path TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    include INTEGER NOT NULL DEFAULT 1
);
"""

LIVE = "deleted_at IS NULL"


@dataclass
class Image:
    id: int | None
    path: str  # 相對於 data_dir
    original_name: str = ""
    include: bool = True


@dataclass
class Entry:
    id: int | None
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    text: str
    status: str
    tags: list[str] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    carried_from: int | None = None  # 從哪一筆延續（帶入或追加進度）
    category_id: int | None = None
    category: str = ""  # 分類名稱，讀取時填入
    uuid: str = ""  # 新增時自動產生
    title: str = ""  # 可省略
    priority: int = DEFAULT_PRIORITY

    @property
    def summary(self) -> str:
        """清單、提示中顯示的一行：有標題用標題，否則用內容第一行。"""
        if self.title:
            return self.title
        return self.text.strip().splitlines()[0] if self.text.strip() else ""


def normalize_tag(tag: str) -> str:
    return tag.strip().lstrip("#").strip()


def parse_tags(text: str) -> list[str]:
    """把 "#專案A #bug, 會議" 拆成 ["專案A", "bug", "會議"]，去重並保留順序。"""
    tags = []
    for part in re.split(r"[\s,，、]+", text):
        tag = normalize_tag(part)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def format_utc(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def utc_now() -> str:
    return format_utc(datetime.now(timezone.utc))


def _to_utc(value) -> str:
    """把舊版的本地時間字串轉成 UTC 格式；看不懂的就用現在時間。"""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return utc_now()
    return format_utc(dt if dt.tzinfo else dt.astimezone())


def new_uuid() -> str:
    return str(uuid.uuid4())


class Database:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.data_dir / "reporttool.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    # ---- 資料庫升級 ----

    def _columns(self, table: str) -> set[str]:
        return {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _migrate(self):
        """依 PRAGMA user_version 逐步升級。每一步都會先檢查欄位，重複執行也安全。"""
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        steps = {1: self._migrate_v1, 2: self._migrate_v2, 3: self._migrate_v3,
                 4: self._migrate_v4}
        for target in range(version + 1, SCHEMA_VERSION + 1):
            with self.conn:
                steps[target]()
                self.conn.execute(f"PRAGMA user_version = {target}")

    def _migrate_v1(self):
        """分類欄位與預設分類。"""
        if "category_id" not in self._columns("entries"):
            self.conn.execute("ALTER TABLE entries ADD COLUMN category_id INTEGER REFERENCES categories(id)")
            for pos, name in enumerate(DEFAULT_CATEGORIES):
                self.conn.execute("INSERT OR IGNORE INTO categories (name, position) VALUES (?, ?)", (name, pos))

    def _migrate_v2(self):
        """同步準備：uuid、UTC 時間、tombstone、裝置 id。"""
        if "uuid" not in self._columns("entries"):
            self.conn.execute("ALTER TABLE entries ADD COLUMN uuid TEXT")
            self.conn.execute("ALTER TABLE entries ADD COLUMN deleted_at TEXT")
        if "updated_at" not in self._columns("categories"):
            self.conn.execute("ALTER TABLE categories ADD COLUMN updated_at TEXT")
            self.conn.execute("ALTER TABLE categories ADD COLUMN deleted_at TEXT")
        self.conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

        for row in self.conn.execute("SELECT id, uuid, created_at, updated_at FROM entries").fetchall():
            self.conn.execute(
                "UPDATE entries SET uuid=?, created_at=?, updated_at=? WHERE id=?",
                (row["uuid"] or new_uuid(), _to_utc(row["created_at"]), _to_utc(row["updated_at"]), row["id"]))
        self.conn.execute("UPDATE categories SET updated_at=? WHERE updated_at IS NULL", (utc_now(),))
        for row in self.conn.execute("SELECT name, last_used FROM tags").fetchall():
            self.conn.execute("UPDATE tags SET last_used=? WHERE name=?", (_to_utc(row["last_used"]), row["name"]))

        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_uuid ON entries(uuid)")
        self.conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('device_id', ?)", (new_uuid(),))

    def _migrate_v3(self):
        """紀錄標題。"""
        if "title" not in self._columns("entries"):
            self.conn.execute("ALTER TABLE entries ADD COLUMN title TEXT NOT NULL DEFAULT ''")

    def _migrate_v4(self):
        """執行優先順序（1~5，1 最優先）。"""
        if "priority" not in self._columns("entries"):
            self.conn.execute(f"ALTER TABLE entries ADD COLUMN priority INTEGER NOT NULL DEFAULT {DEFAULT_PRIORITY}")

    @property
    def device_id(self) -> str:
        return self.conn.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()[0]

    def close(self):
        self.conn.close()

    # ---- 圖片檔案 ----

    def abs_path(self, rel_path: str) -> Path:
        return self.data_dir / rel_path

    def new_image_path(self, date: str, suffix: str) -> str:
        """配置一個新的圖片存放位置（相對路徑），並建立所需資料夾。"""
        rel = Path("images") / date / f"{uuid.uuid4().hex}{suffix.lower()}"
        (self.data_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        return rel.as_posix()

    def import_image_file(self, src, date: str) -> Image:
        src = Path(src)
        rel = self.new_image_path(date, src.suffix or ".png")
        shutil.copy2(src, self.abs_path(rel))
        return Image(None, rel, src.stem)

    def _remove_files(self, rel_paths):
        for rel in rel_paths:
            try:
                self.abs_path(rel).unlink()
            except FileNotFoundError:
                pass

    # ---- 紀錄 ----

    def _add_tag_use(self, tag: str, now: str):
        self.conn.execute(
            "INSERT INTO tags (name, use_count, last_used) VALUES (?, 1, ?)"
            " ON CONFLICT(name) DO UPDATE SET use_count = use_count + 1, last_used = MAX(last_used, excluded.last_used)",
            (tag, now),
        )

    def save_entry(self, entry: Entry) -> int:
        """新增或更新一筆紀錄（含 tag 與圖片），回傳 id。"""
        now = utc_now()
        removed_files = []
        with self.conn:
            if entry.id is None:
                entry.uuid = entry.uuid or new_uuid()
                cur = self.conn.execute(
                    "INSERT INTO entries (uuid, date, time, title, text, status, priority, carried_from,"
                    " category_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (entry.uuid, entry.date, entry.time, entry.title, entry.text, entry.status, entry.priority,
                     entry.carried_from, entry.category_id, now, now),
                )
                entry.id = cur.lastrowid
                old_tags = set()
            else:
                self.conn.execute(
                    "UPDATE entries SET date=?, time=?, title=?, text=?, status=?, priority=?, category_id=?,"
                    " updated_at=? WHERE id=?",
                    (entry.date, entry.time, entry.title, entry.text, entry.status, entry.priority, entry.category_id,
                     now, entry.id),
                )
                old_tags = {r["tag"] for r in self.conn.execute(
                    "SELECT tag FROM entry_tags WHERE entry_id=?", (entry.id,))}
                keep = {img.id for img in entry.images if img.id is not None}
                for row in self.conn.execute(
                        "SELECT id, path FROM images WHERE entry_id=?", (entry.id,)).fetchall():
                    if row["id"] not in keep:
                        self.conn.execute("DELETE FROM images WHERE id=?", (row["id"],))
                        removed_files.append(row["path"])

            self.conn.execute("DELETE FROM entry_tags WHERE entry_id=?", (entry.id,))
            for tag in entry.tags:
                self.conn.execute("INSERT INTO entry_tags (entry_id, tag) VALUES (?, ?)", (entry.id, tag))
                if tag not in old_tags:
                    self._add_tag_use(tag, now)

            for pos, img in enumerate(entry.images):
                if img.id is None:
                    cur = self.conn.execute(
                        "INSERT INTO images (entry_id, position, path, original_name, include) VALUES (?, ?, ?, ?, ?)",
                        (entry.id, pos, img.path, img.original_name, int(img.include)),
                    )
                    img.id = cur.lastrowid
                else:
                    self.conn.execute(
                        "UPDATE images SET position=?, include=? WHERE id=?", (pos, int(img.include), img.id))
        self._remove_files(removed_files)
        return entry.id

    def _tombstone(self, entry_id: int, updated_at: str) -> list[str]:
        """把紀錄變成 tombstone：清掉內容、tag 與圖片，只留 uuid 與刪除時間。回傳要刪的圖片檔。"""
        paths = [r["path"] for r in self.conn.execute("SELECT path FROM images WHERE entry_id=?", (entry_id,))]
        self.conn.execute("DELETE FROM images WHERE entry_id=?", (entry_id,))
        self.conn.execute("DELETE FROM entry_tags WHERE entry_id=?", (entry_id,))
        self.conn.execute(
            "UPDATE entries SET title='', text='', category_id=NULL, deleted_at=?, updated_at=? WHERE id=?",
            (updated_at, updated_at, entry_id))
        return paths

    def delete_entry(self, entry_id: int):
        with self.conn:
            paths = self._tombstone(entry_id, utc_now())
        self._remove_files(paths)

    def _load(self, row) -> Entry:
        tags = [r["tag"] for r in self.conn.execute(
            "SELECT tag FROM entry_tags WHERE entry_id=? ORDER BY rowid", (row["id"],))]
        images = [Image(r["id"], r["path"], r["original_name"], bool(r["include"])) for r in self.conn.execute(
            "SELECT * FROM images WHERE entry_id=? ORDER BY position", (row["id"],))]
        category = self.conn.execute(
            "SELECT name FROM categories WHERE id=?", (row["category_id"],)).fetchone()
        return Entry(row["id"], row["date"], row["time"], row["text"], row["status"], tags, images,
                     row["carried_from"], row["category_id"], category["name"] if category else "", row["uuid"],
                     row["title"], row["priority"])

    def get_entry(self, entry_id: int) -> Entry | None:
        row = self.conn.execute(f"SELECT * FROM entries WHERE id=? AND {LIVE}", (entry_id,)).fetchone()
        return self._load(row) if row else None

    def last_entry(self) -> Entry | None:
        """最近一次新增的紀錄，用來預設新紀錄的分類與 tag。"""
        row = self.conn.execute(
            f"SELECT * FROM entries WHERE {LIVE} ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        return self._load(row) if row else None

    def set_status(self, entry_id: int, status: str):
        with self.conn:
            self.conn.execute("UPDATE entries SET status=?, updated_at=? WHERE id=?", (status, utc_now(), entry_id))

    def history(self, entry_id: int | None) -> list[Entry]:
        """沿著 carried_from 往前找這件工作先前的紀錄，由舊到新，不含 entry_id 本身。"""
        chain = []
        seen = set()
        current = self.get_entry(entry_id) if entry_id else None
        while current and current.carried_from and current.carried_from not in seen:
            seen.add(current.carried_from)
            current = self.get_entry(current.carried_from)
            if current:
                chain.append(current)
        return chain[::-1]

    def entries_for_date(self, date: str) -> list[Entry]:
        return self.entries_between(date, date)

    def entries_between(self, start: str, end: str) -> list[Entry]:
        """取得日期區間（含頭尾）內的紀錄，供週報等功能使用。"""
        rows = self.conn.execute(
            f"SELECT * FROM entries WHERE date BETWEEN ? AND ? AND {LIVE} ORDER BY date, time, created_at, id",
            (start, end)).fetchall()
        return [self._load(r) for r in rows]

    # ---- 標籤 ----

    def all_tags(self) -> list[str]:
        return [r["name"] for r in self.conn.execute("SELECT name FROM tags ORDER BY use_count DESC, name")]

    def recent_tags(self, limit: int = 8) -> list[str]:
        return [r["name"] for r in self.conn.execute(
            "SELECT name FROM tags ORDER BY last_used DESC, use_count DESC LIMIT ?", (limit,))]

    def tag_usage(self) -> list[tuple[str, int]]:
        """所有 tag 與使用中的紀錄數。"""
        return [(r["name"], r["n"]) for r in self.conn.execute(
            "SELECT t.name, COUNT(et.entry_id) AS n FROM tags t LEFT JOIN entry_tags et ON et.tag = t.name"
            " GROUP BY t.name ORDER BY n DESC, t.name")]

    def _touch_entries_with_tag(self, tag: str):
        """tag 改名或刪除會改變紀錄內容，更新 updated_at 讓同步帶過去。"""
        self.conn.execute(
            "UPDATE entries SET updated_at=? WHERE id IN (SELECT entry_id FROM entry_tags WHERE tag=?)",
            (utc_now(), tag))

    def rename_tag(self, old: str, new: str):
        """改名；新名稱已存在時等於合併。"""
        new = normalize_tag(new)
        if not new or new == old:
            return
        with self.conn:
            self._touch_entries_with_tag(old)
            # 同時有新舊兩個 tag 的紀錄，只留新的
            self.conn.execute(
                "DELETE FROM entry_tags WHERE tag=? AND entry_id IN (SELECT entry_id FROM entry_tags WHERE tag=?)",
                (old, new))
            self.conn.execute("UPDATE entry_tags SET tag=? WHERE tag=?", (new, old))
            row = self.conn.execute("SELECT use_count, last_used FROM tags WHERE name=?", (old,)).fetchone()
            if row:
                self.conn.execute(
                    "INSERT INTO tags (name, use_count, last_used) VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET"
                    " use_count = use_count + excluded.use_count, last_used = MAX(last_used, excluded.last_used)",
                    (new, row["use_count"], row["last_used"]))
                self.conn.execute("DELETE FROM tags WHERE name=?", (old,))

    def delete_tag(self, name: str):
        """從所有紀錄與建議清單中移除。"""
        with self.conn:
            self._touch_entries_with_tag(name)
            self.conn.execute("DELETE FROM entry_tags WHERE tag=?", (name,))
            self.conn.execute("DELETE FROM tags WHERE name=?", (name,))

    # ---- 分類 ----

    def categories(self) -> list[tuple[int, str]]:
        return [(r["id"], r["name"]) for r in self.conn.execute(
            f"SELECT id, name FROM categories WHERE {LIVE} ORDER BY position, id")]

    def category_usage(self, category_id: int) -> int:
        return self.conn.execute(
            f"SELECT COUNT(*) FROM entries WHERE category_id=? AND {LIVE}", (category_id,)).fetchone()[0]

    def _check_category_name(self, name: str, exclude_id: int = -1) -> str:
        name = name.strip()
        if not name:
            raise ValueError("分類名稱不能是空的")
        if self.conn.execute(
                f"SELECT 1 FROM categories WHERE name=? AND id<>? AND {LIVE}", (name, exclude_id)).fetchone():
            raise ValueError(f"已經有「{name}」這個分類")
        return name

    def _next_category_position(self) -> int:
        return self.conn.execute(
            f"SELECT COALESCE(MAX(position), -1) + 1 FROM categories WHERE {LIVE}").fetchone()[0]

    def add_category(self, name: str) -> int:
        name = self._check_category_name(name)
        now = utc_now()
        with self.conn:
            pos = self._next_category_position()
            row = self.conn.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
            if row:  # 之前刪掉的同名分類，直接復原
                self.conn.execute("UPDATE categories SET position=?, updated_at=?, deleted_at=NULL WHERE id=?",
                                  (pos, now, row["id"]))
                return row["id"]
            return self.conn.execute("INSERT INTO categories (name, position, updated_at) VALUES (?, ?, ?)",
                                     (name, pos, now)).lastrowid

    def rename_category(self, category_id: int, name: str):
        """改名。分類以名稱辨識，所以舊名稱會留下 tombstone，紀錄也會更新 updated_at。"""
        name = self._check_category_name(name, category_id)
        old = self.conn.execute("SELECT name, position FROM categories WHERE id=?", (category_id,)).fetchone()
        if old is None or old["name"] == name:
            return
        now = utc_now()
        with self.conn:
            stale = self.conn.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
            if stale:  # 同名的 tombstone，被這次改名取代
                self.conn.execute("UPDATE entries SET category_id=NULL WHERE category_id=?", (stale["id"],))
                self.conn.execute("DELETE FROM categories WHERE id=?", (stale["id"],))
            self.conn.execute("UPDATE categories SET name=?, updated_at=? WHERE id=?", (name, now, category_id))
            self.conn.execute(
                "INSERT INTO categories (name, position, updated_at, deleted_at) VALUES (?, ?, ?, ?)",
                (old["name"], old["position"], now, now))
            self.conn.execute("UPDATE entries SET updated_at=? WHERE category_id=?", (now, category_id))

    def delete_category(self, category_id: int):
        """刪除分類，原本屬於它的紀錄變成未分類。"""
        now = utc_now()
        with self.conn:
            self.conn.execute("UPDATE entries SET category_id=NULL, updated_at=? WHERE category_id=?",
                              (now, category_id))
            self.conn.execute("UPDATE categories SET deleted_at=?, updated_at=? WHERE id=?",
                              (now, now, category_id))

    def reorder_categories(self, ids: list[int]):
        now = utc_now()
        with self.conn:
            for pos, cid in enumerate(ids):
                self.conn.execute("UPDATE categories SET position=?, updated_at=? WHERE id=? AND position<>?",
                                  (pos, now, cid, pos))

    # ---- 帶入未完成項目 ----

    def previous_date_with_entries(self, date: str) -> str | None:
        row = self.conn.execute(f"SELECT MAX(date) AS d FROM entries WHERE date < ? AND {LIVE}", (date,)).fetchone()
        return row["d"]

    def carry_over_candidates(self, from_date: str, to_date: str) -> list[Entry]:
        """from_date 中「進行中／卡住」且尚未帶入 to_date 的紀錄。"""
        rows = self.conn.execute(
            "SELECT * FROM entries e WHERE e.date=? AND e.status IN (?, ?) AND e.deleted_at IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM entries c WHERE c.date=? AND c.carried_from=e.id"
            " AND c.deleted_at IS NULL)"
            " ORDER BY e.time, e.id",
            (from_date, *UNFINISHED, to_date),
        ).fetchall()
        return [self._load(r) for r in rows]

    def carry_over(self, from_date: str, to_date: str, time: str | None = None) -> list[int]:
        """把未完成項目複製成 to_date 的新紀錄（不帶圖片），回傳新 id。"""
        time = time or datetime.now().strftime("%H:%M")
        new_ids = []
        for old in self.carry_over_candidates(from_date, to_date):
            new = Entry(None, to_date, time, old.text, old.status, list(old.tags), [], old.id, old.category_id,
                        title=old.title, priority=old.priority)
            new_ids.append(self.save_entry(new))
        return new_ids

"""SQLite 資料層：紀錄、標籤、分類、圖片。

圖片原檔存放在 data_dir/images/YYYY-MM-DD/，資料庫只記相對路徑。

為了之後和手機同步（見 docs/data-format.md）：
- 每筆紀錄有 uuid；跨裝置用 uuid 辨識，本機的整數 id 只在這台電腦有意義
- updated_at 一律是 UTC 毫秒字串（YYYY-MM-DDTHH:MM:SS.mmmZ），同步時以較新的為準
- 刪除紀錄與分類只標記 deleted_at（tombstone），讓另一台裝置知道它被刪了
- 分類是樹狀（分類／子分類），每個節點有 uuid；新建節點的 uuid 由完整路徑算出（uuid5），
  兩台裝置各自建立或轉換出相同路徑的分類時，uuid 相同，同步時會自然合併
"""
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# 依工作流程排列：輸入視窗的按鈕、Alt+1~6、右鍵選單、狀態列都用這個順序
STATUSES = ("todo", "doing", "waiting", "review", "done", "cancelled")
STATUS_LABELS = {"todo": "待辦", "doing": "進行中", "waiting": "等待中", "review": "待確認", "done": "完成",
                 "cancelled": "取消"}
STATUS_ICONS = {"todo": "📝", "doing": "🔄", "waiting": "⏳", "review": "👀", "done": "✅", "cancelled": "🚫"}
# 回報、投影片內的排序：先講成果，取消的放最後
REPORT_ORDER = {s: i for i, s in enumerate(("done", "review", "doing", "waiting", "todo", "cancelled"))}
UNFINISHED = ("todo", "doing", "waiting", "review")
LEGACY_STATUSES = {"blocked": "todo"}  # 舊版的「卡住」改成「待辦」
DEFAULT_CATEGORIES = ("開發", "會議", "支援", "行政", "其他")
PRIORITIES = (1, 2, 3, 4, 5)  # 1 最優先
DEFAULT_PRIORITY = 3
SCHEMA_VERSION = 7
CATEGORY_SEP = " › "
CATEGORY_NAMESPACE = uuid.UUID("7d4f2a3e-5b1c-4e8a-9f60-2c3d4e5f6a7b")

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
# 程式自動建立的預設分類用最早的時間，匯入備份時對方的設定（排序、進度）一定優先
EPOCH = "1970-01-01T00:00:00.000Z"

CATEGORIES_V6 = """
CREATE TABLE categories_v6 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES categories(id),
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    progress INTEGER,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
)
"""


def category_uuid(path: list[str]) -> str:
    """新建分類的 uuid：由完整路徑算出，各裝置建立相同路徑時會得到同一個 uuid。"""
    return str(uuid.uuid5(CATEGORY_NAMESPACE, "\n".join(path)))


@dataclass
class Category:
    id: int
    uuid: str
    parent_id: int | None
    name: str
    position: int
    progress: int | None  # 0~100，None 表示沒有設定進度
    path: list[str]
    priority: int = DEFAULT_PRIORITY  # 1~5，同一層先依優先度、再依 position 排列

    @property
    def depth(self) -> int:
        return len(self.path) - 1

    @property
    def label(self) -> str:
        return CATEGORY_SEP.join(self.path)


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
    category: str = ""  # 分類的完整路徑（例如「開發 › 專案A」），讀取時填入
    uuid: str = ""  # 新增時自動產生
    title: str = ""  # 可省略
    priority: int = DEFAULT_PRIORITY

    @property
    def summary(self) -> str:
        """清單、提示中顯示的一行：有標題用標題，否則用內容第一行。"""
        if self.title:
            return self.title
        return self.text.strip().splitlines()[0] if self.text.strip() else ""


def same_level_key(entry: Entry) -> tuple:
    """同一層的紀錄：優先順序高（數字小）的在前，相同時依標題（沒有標題用內容第一行），再依時間。"""
    return entry.priority, entry.summary, entry.time


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
                 4: self._migrate_v4, 5: self._migrate_v5, 6: self._migrate_v6,
                 7: self._migrate_v7}
        has_entries = self.conn.execute("SELECT 1 FROM entries LIMIT 1").fetchone() is not None
        if version < 6 and has_entries:  # 分類改成樹狀是較大的改動，升級前先留一份備份
            self.conn.commit()
            shutil.copy2(self.data_dir / "reporttool.db", self.data_dir / "reporttool-before-v6.db")
        # 重建資料表時要暫時關掉外鍵檢查（不能在交易中切換）
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            for target in range(version + 1, SCHEMA_VERSION + 1):
                with self.conn:
                    steps[target]()
                    self.conn.execute(f"PRAGMA user_version = {target}")
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

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
        self.conn.execute("UPDATE categories SET updated_at=? WHERE updated_at IS NULL", (EPOCH,))
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

    def _migrate_v5(self):
        """狀態改版：「卡住」改成「待辦」。不更新 updated_at，每台裝置升級後結果相同，不會被當成新的修改。"""
        for old, new in LEGACY_STATUSES.items():
            self.conn.execute("UPDATE entries SET status=? WHERE status=?", (new, old))

    def _migrate_v6(self):
        """分類改成樹狀：分類變成第一層，每筆紀錄的第一個 tag 變成它所屬分類底下的子分類。

        tag 本身原樣保留。不更新紀錄的 updated_at：每台裝置轉換的結果（含分類 uuid）都相同。
        """
        if "parent_id" not in self._columns("categories"):
            self.conn.execute(CATEGORIES_V6)
            for r in self.conn.execute("SELECT * FROM categories").fetchall():
                self.conn.execute(
                    "INSERT INTO categories_v6 (id, uuid, parent_id, name, position, updated_at, deleted_at)"
                    " VALUES (?, ?, NULL, ?, ?, ?, ?)",
                    (r["id"], category_uuid([r["name"]]), r["name"], r["position"], r["updated_at"] or EPOCH,
                     r["deleted_at"]))
            self.conn.execute("DROP TABLE categories")
            self.conn.execute("ALTER TABLE categories_v6 RENAME TO categories")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id)")
        convert_first_tags(self)

    def _migrate_v7(self):
        """分類優先度（1~5，1 最優先）。"""
        if "priority" not in self._columns("categories"):
            self.conn.execute(
                f"ALTER TABLE categories ADD COLUMN priority INTEGER NOT NULL DEFAULT {DEFAULT_PRIORITY}")

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
        return Entry(row["id"], row["date"], row["time"], row["text"], row["status"], tags, images,
                     row["carried_from"], row["category_id"], self.category_label(row["category_id"]), row["uuid"],
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

    # ---- 分類（樹狀：分類／子分類）----

    def _live_category(self, category_id: int | None) -> bool:
        return category_id is not None and self.conn.execute(
            f"SELECT 1 FROM categories WHERE id=? AND {LIVE}", (category_id,)).fetchone() is not None

    def category_path(self, category_id: int | None) -> list[str]:
        """從第一層到這個節點的名稱；不存在時回傳空清單。"""
        if category_id is None:
            return []
        rows = self.conn.execute(
            "WITH RECURSIVE up(id, parent_id, name, depth) AS ("
            " SELECT id, parent_id, name, 0 FROM categories WHERE id=?"
            " UNION ALL SELECT c.id, c.parent_id, c.name, up.depth + 1 FROM categories c"
            " JOIN up ON c.id = up.parent_id WHERE up.depth < 50)"
            " SELECT name FROM up ORDER BY depth DESC", (category_id,)).fetchall()
        return [r["name"] for r in rows]

    def category_label(self, category_id: int | None) -> str:
        return CATEGORY_SEP.join(self.category_path(category_id))

    def category_tree(self) -> list[Category]:
        """所有分類，依樹狀順序（先父後子；同層先依優先度，再照 position）排成一列。上層已刪除的節點當作第一層。"""
        rows = self.conn.execute(
            f"SELECT * FROM categories WHERE {LIVE} ORDER BY priority, position, id").fetchall()
        ids = {r["id"] for r in rows}
        children: dict[int | None, list] = {}
        for r in rows:
            children.setdefault(r["parent_id"] if r["parent_id"] in ids else None, []).append(r)
        result = []

        def walk(parent_id, path):
            for r in children.get(parent_id, []):
                node_path = path + [r["name"]]
                result.append(Category(r["id"], r["uuid"], r["parent_id"] if r["parent_id"] in ids else None,
                                       r["name"], r["position"], r["progress"], node_path, r["priority"]))
                if len(node_path) < 50:
                    walk(r["id"], node_path)

        walk(None, [])
        return result

    def categories(self) -> list[tuple[int, str]]:
        """（id, 完整路徑），依樹狀順序。"""
        return [(c.id, c.label) for c in self.category_tree()]

    def category_subtree(self, category_id: int) -> list[int]:
        """這個節點與底下所有子分類的 id。"""
        return [r["id"] for r in self.conn.execute(
            "WITH RECURSIVE down(id, depth) AS (SELECT ?, 0"
            " UNION ALL SELECT c.id, down.depth + 1 FROM categories c JOIN down ON c.parent_id = down.id"
            f" WHERE c.{LIVE} AND down.depth < 50) SELECT id FROM down", (category_id,))]

    def category_usage(self, category_id: int) -> int:
        return self.conn.execute(
            f"SELECT COUNT(*) FROM entries WHERE category_id=? AND {LIVE}", (category_id,)).fetchone()[0]

    def _check_category_name(self, name: str, parent_id: int | None, exclude_id: int = -1) -> str:
        name = name.strip()
        if not name:
            raise ValueError("分類名稱不能是空的")
        if CATEGORY_SEP.strip() in name:
            raise ValueError(f"分類名稱不能包含「{CATEGORY_SEP.strip()}」")
        if self.conn.execute(
                f"SELECT 1 FROM categories WHERE name=? AND parent_id IS ? AND id<>? AND {LIVE}",
                (name, parent_id, exclude_id)).fetchone():
            raise ValueError(f"同一層已經有「{name}」")
        return name

    def _next_category_position(self, parent_id: int | None = None) -> int:
        return self.conn.execute(
            f"SELECT COALESCE(MAX(position), -1) + 1 FROM categories WHERE parent_id IS ? AND {LIVE}",
            (parent_id,)).fetchone()[0]

    def _ensure_category(self, parent_id: int | None, name: str, now: str) -> int:
        """找到同一層同名的分類；沒有就建立（之前刪掉的同路徑分類會直接復原）。不開交易。"""
        row = self.conn.execute(
            f"SELECT id FROM categories WHERE name=? AND parent_id IS ? AND {LIVE}", (name, parent_id)).fetchone()
        if row:
            return row["id"]
        node_uuid = category_uuid(self.category_path(parent_id) + [name])
        pos = self._next_category_position(parent_id)
        old = self.conn.execute("SELECT id, deleted_at FROM categories WHERE uuid=?", (node_uuid,)).fetchone()
        if old and old["deleted_at"]:
            self.conn.execute(
                "UPDATE categories SET parent_id=?, name=?, position=?, updated_at=?, deleted_at=NULL WHERE id=?",
                (parent_id, name, pos, now, old["id"]))
            return old["id"]
        if old:  # 這個路徑的 uuid 已經被改名或移動後的分類用掉了
            node_uuid = new_uuid()
        return self.conn.execute(
            "INSERT INTO categories (uuid, parent_id, name, position, updated_at) VALUES (?, ?, ?, ?, ?)",
            (node_uuid, parent_id, name, pos, now)).lastrowid

    def add_category(self, name: str, parent_id: int | None = None) -> int:
        """新增分類；parent_id 有值時是子分類。"""
        name = self._check_category_name(name, parent_id)
        with self.conn:
            return self._ensure_category(parent_id, name, utc_now())

    def rename_category(self, category_id: int, name: str):
        """改名。分類以 uuid 辨識，紀錄不用跟著改。"""
        row = self.conn.execute("SELECT parent_id, name FROM categories WHERE id=?", (category_id,)).fetchone()
        if row is None:
            return
        name = self._check_category_name(name, row["parent_id"], category_id)
        if name == row["name"]:
            return
        with self.conn:
            self.conn.execute("UPDATE categories SET name=?, updated_at=? WHERE id=?", (name, utc_now(), category_id))

    def move_category(self, category_id: int, parent_id: int | None):
        """移到另一個分類底下（parent_id=None 表示移到第一層），放在新位置的最後。"""
        row = self.conn.execute("SELECT parent_id, name FROM categories WHERE id=?", (category_id,)).fetchone()
        if row is None or row["parent_id"] == parent_id:
            return
        if parent_id is not None and parent_id in self.category_subtree(category_id):
            raise ValueError("不能移到自己或自己的子分類底下")
        self._check_category_name(row["name"], parent_id, category_id)
        with self.conn:
            self.conn.execute("UPDATE categories SET parent_id=?, position=?, updated_at=? WHERE id=?",
                              (parent_id, self._next_category_position(parent_id), utc_now(), category_id))

    def delete_category(self, category_id: int):
        """刪除分類與底下所有子分類；這些分類中的紀錄移到被刪分類的上一層（第一層就變成未分類）。"""
        row = self.conn.execute("SELECT parent_id FROM categories WHERE id=?", (category_id,)).fetchone()
        if row is None:
            return
        target = row["parent_id"] if self._live_category(row["parent_id"]) else None
        subtree = self.category_subtree(category_id)
        marks = ", ".join("?" * len(subtree))
        now = utc_now()
        with self.conn:
            self.conn.execute(f"UPDATE entries SET category_id=?, updated_at=? WHERE category_id IN ({marks})",
                              (target, now, *subtree))
            self.conn.execute(f"UPDATE categories SET deleted_at=?, updated_at=? WHERE id IN ({marks})",
                              (now, now, *subtree))

    def reorder_categories(self, ids: list[int]):
        """依 ids 的先後重排；只改同一層之間的順序，不會改變層級。"""
        now = utc_now()
        counters: dict = {}
        with self.conn:
            for cid in ids:
                row = self.conn.execute("SELECT parent_id FROM categories WHERE id=?", (cid,)).fetchone()
                if row is None:
                    continue
                pos = counters.setdefault(row["parent_id"], 0)
                counters[row["parent_id"]] = pos + 1
                self.conn.execute("UPDATE categories SET position=?, updated_at=? WHERE id=? AND position<>?",
                                  (pos, now, cid, pos))

    def set_category_progress(self, category_id: int, progress: int | None):
        """設定進度 0~100；None 表示不顯示進度。"""
        if progress is not None and (isinstance(progress, bool) or not isinstance(progress, int)
                                     or not 0 <= progress <= 100):
            raise ValueError("進度要是 0～100 的整數")
        with self.conn:
            self.conn.execute("UPDATE categories SET progress=?, updated_at=? WHERE id=? AND progress IS NOT ?",
                              (progress, utc_now(), category_id, progress))

    def set_category_priority(self, category_id: int, priority: int):
        """設定優先度 1~5（1 最優先）；同一層的分類先依優先度、再依手動順序排列。"""
        if isinstance(priority, bool) or priority not in PRIORITIES:
            raise ValueError("優先度要是 1～5")
        with self.conn:
            self.conn.execute("UPDATE categories SET priority=?, updated_at=? WHERE id=? AND priority<>?",
                              (priority, utc_now(), category_id, priority))

    # ---- 帶入未完成項目 ----

    def previous_date_with_entries(self, date: str) -> str | None:
        row = self.conn.execute(f"SELECT MAX(date) AS d FROM entries WHERE date < ? AND {LIVE}", (date,)).fetchone()
        return row["d"]

    def carry_over_candidates(self, from_date: str, to_date: str) -> list[Entry]:
        """from_date 中未完成（不是完成或取消）、尚未帶入 to_date，而且是這件工作最新一筆的紀錄。

        同一件工作（carried_from 串起來的紀錄）只要已經有比它新的紀錄（例如同一天追加了進度、
        或從更早的紀錄追加並標成完成），就以新的那筆為準，不再帶入舊的。"""
        marks = ", ".join("?" * len(UNFINISHED))
        rows = self.conn.execute(
            f"SELECT * FROM entries e WHERE e.date=? AND e.status IN ({marks}) AND e.deleted_at IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM entries c WHERE c.date=? AND c.carried_from=e.id"
            " AND c.deleted_at IS NULL)"
            " ORDER BY e.time, e.id",
            (from_date, *UNFINISHED, to_date),
        ).fetchall()
        if not rows:
            return []
        newer = self._superseded_entries()
        return [self._load(r) for r in rows if r["id"] not in newer]

    def _superseded_entries(self) -> set[int]:
        """同一件工作中，已經有更新紀錄（日期、時間較晚）的那些紀錄 id。"""
        rows = self.conn.execute("SELECT id, carried_from, date, time, deleted_at FROM entries").fetchall()
        parent = {r["id"]: r["carried_from"] for r in rows}
        children: dict[int, list[int]] = {}
        for r in rows:
            if r["carried_from"] is not None:
                children.setdefault(r["carried_from"], []).append(r["id"])
        key = {r["id"]: (r["date"], r["time"], r["id"]) for r in rows if r["deleted_at"] is None}

        def root(i):
            seen = {i}
            while parent.get(i) is not None and parent[i] in parent and parent[i] not in seen:
                i = parent[i]
                seen.add(i)
            return i

        superseded = set()
        done_roots = set()
        for r in rows:
            top = root(r["id"])
            if top in done_roots:
                continue
            done_roots.add(top)
            members, stack = [], [top]
            seen = {top}
            while stack:
                i = stack.pop()
                if i in key:
                    members.append(i)
                for c in children.get(i, []):
                    if c not in seen:
                        seen.add(c)
                        stack.append(c)
            if len(members) > 1:
                latest = max(members, key=key.get)
                superseded.update(m for m in members if m != latest)
        return superseded

    def carry_over(self, from_date: str, to_date: str, time: str | None = None) -> list[int]:
        """把未完成項目複製成 to_date 的新紀錄（不帶圖片），回傳新 id。"""
        time = time or datetime.now().strftime("%H:%M")
        new_ids = []
        for old in self.carry_over_candidates(from_date, to_date):
            new = Entry(None, to_date, time, old.text, old.status, list(old.tags), [], old.id, old.category_id,
                        title=old.title, priority=old.priority)
            new_ids.append(self.save_entry(new))
        return new_ids


def convert_first_tags(db: "Database", entry_ids: list[int] | None = None):
    """舊資料轉換：紀錄的第一個 tag 變成它所屬分類底下的子分類（沒有分類就變成第一層）。不開交易、不更新 updated_at。"""
    rows = db.conn.execute(
        f"SELECT e.id, e.category_id, e.updated_at, (SELECT tag FROM entry_tags t WHERE t.entry_id = e.id"
        f" ORDER BY t.rowid LIMIT 1) AS tag FROM entries e WHERE e.{LIVE}").fetchall()
    for r in rows:
        if r["tag"] and (entry_ids is None or r["id"] in entry_ids):
            parent = r["category_id"] if db._live_category(r["category_id"]) else None
            child = db._ensure_category(parent, r["tag"], r["updated_at"])
            db.conn.execute("UPDATE entries SET category_id=? WHERE id=?", (child, r["id"]))

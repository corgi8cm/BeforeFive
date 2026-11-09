from PIL import Image as PILImage

from reporttool.config import DEFAULT_INSTRUCTIONS
from reporttool.db import Database, Entry
from reporttool.report import generate_report, slugify

DATE = "2026-11-02"


def make_image(path, size):
    PILImage.new("RGB", size, "blue").save(path)
    return path


def test_slugify():
    assert slugify("修登入頁 timeout 問題") == "修登入頁timeout問題"
    assert slugify('a/b:c*?"<>|d\n第二行') == "abcd"
    assert slugify("   ") == "image"
    assert len(slugify("很" * 50)) == 20


def test_generate_report(tmp_path):
    db = Database(tmp_path / "data")
    big = db.import_image_file(make_image(tmp_path / "big.png", (3200, 1000)), DATE)
    small = db.import_image_file(make_image(tmp_path / "small.jpg", (800, 600)), DATE)
    hidden = db.import_image_file(make_image(tmp_path / "secret.png", (100, 100)), DATE)
    hidden.include = False

    db.save_entry(Entry(None, DATE, "14:05", "timeout 修好，已送 PR", "done", ["專案A"], [small, hidden]))
    db.save_entry(Entry(None, DATE, "10:20", "修登入頁 timeout 問題", "doing", ["專案A"], [big]))
    db.save_entry(Entry(None, DATE, "16:00", "等待 DBA 開權限\n已經寄信催了", "waiting", []))

    folder = generate_report(db, DATE, tmp_path / "out", DEFAULT_INSTRUCTIONS)
    assert folder.name == f"report_{DATE}"

    files = sorted(p.name for p in folder.iterdir())
    assert files == ["01_修登入頁timeout問題.png", "02_timeout修好，已送PR.jpg", "report.md"]

    with PILImage.open(folder / "01_修登入頁timeout問題.png") as im:
        assert im.size == (1600, 500)
    with PILImage.open(folder / "02_timeout修好，已送PR.jpg") as im:
        assert im.size == (800, 600)  # 小圖不放大

    md = (folder / "report.md").read_text(encoding="utf-8")
    assert md.startswith(f"# 工作回報整理請求（{DATE}）\n\n## 指示\n- 依紀錄中的 [分類：…]")
    assert "- [10:20][進行中][優先3][#專案A] 修登入頁 timeout 問題（![附圖1]）" in md
    assert "- [14:05][完成][優先3][#專案A] timeout 修好，已送 PR（![附圖2]、附圖3：未提供）" in md
    assert md.endswith("\n\n[附圖1]: <01_修登入頁timeout問題.png>\n[附圖2]: <02_timeout修好，已送PR.jpg>\n")
    assert "- [16:00][等待中][優先3] 等待 DBA 開權限\n  已經寄信催了" in md


def test_regenerate_removes_old_images_only(tmp_path):
    db = Database(tmp_path / "data")
    img = db.import_image_file(make_image(tmp_path / "a.png", (10, 10)), DATE)
    entry_id = db.save_entry(Entry(None, DATE, "10:00", "第一版", "done", [], [img]))
    folder = generate_report(db, DATE, tmp_path / "out", "- x")
    (folder / "我的筆記.txt").write_text("keep", encoding="utf-8")

    entry = db.get_entry(entry_id)
    entry.images[0].include = False
    db.save_entry(entry)
    generate_report(db, DATE, tmp_path / "out", "- x")

    assert sorted(p.name for p in folder.iterdir()) == ["report.md", "我的筆記.txt"]


def test_category_and_continuation_in_line(tmp_path):
    db = Database(tmp_path / "data")
    cid = db.categories()[0][0]
    first = db.save_entry(Entry(None, "2026-10-30", "09:00", "修登入頁", "doing", ["專案A"], category_id=cid))
    db.save_entry(Entry(None, DATE, "10:00", "修好了", "done", ["專案A"], carried_from=first, category_id=cid))

    md = (generate_report(db, DATE, tmp_path / "out", "- x") / "report.md").read_text(encoding="utf-8")
    assert "- [10:00][完成][優先3][分類：開發][#專案A] 修好了（延續自 10-30）" in md

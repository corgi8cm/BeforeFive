from PIL import Image as PILImage

from reporttool.db import Database, Entry, parse_tags


def make_png(path, size=(100, 50)):
    PILImage.new("RGB", size, "red").save(path)
    return path


def test_parse_tags():
    assert parse_tags("#專案A  #bug, 會議、#專案A") == ["專案A", "bug", "會議"]
    assert parse_tags("") == []


def test_save_load_update_delete(tmp_path):
    db = Database(tmp_path / "data")
    img = db.import_image_file(make_png(tmp_path / "a.png"), "2026-11-02")
    entry_id = db.save_entry(Entry(None, "2026-11-02", "10:20", "修登入頁", "doing", ["專案A"], [img]))

    loaded = db.get_entry(entry_id)
    assert loaded.text == "修登入頁" and loaded.tags == ["專案A"]
    assert len(loaded.images) == 1 and loaded.images[0].include
    stored = db.abs_path(loaded.images[0].path)
    assert stored.exists()

    loaded.status = "done"
    loaded.images[0].include = False
    db.save_entry(loaded)
    again = db.get_entry(entry_id)
    assert again.status == "done" and not again.images[0].include

    # 從紀錄移除圖片時，原檔也要刪掉
    again.images = []
    db.save_entry(again)
    assert not stored.exists()

    db.delete_entry(entry_id)
    assert db.get_entry(entry_id) is None
    assert db.entries_for_date("2026-11-02") == []


def test_tags_are_remembered(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, "2026-11-01", "09:00", "a", "done", ["舊的"]))
    db.save_entry(Entry(None, "2026-11-02", "09:00", "b", "done", ["專案A", "bug"]))
    db.save_entry(Entry(None, "2026-11-02", "10:00", "c", "done", ["專案A"]))
    assert db.all_tags()[0] == "專案A"
    assert set(db.recent_tags()) == {"舊的", "專案A", "bug"}


def test_entries_sorted_by_time(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, "2026-11-02", "14:00", "下午", "done"))
    db.save_entry(Entry(None, "2026-11-02", "09:00", "早上", "done"))
    assert [e.text for e in db.entries_for_date("2026-11-02")] == ["早上", "下午"]


def test_carry_over(tmp_path):
    db = Database(tmp_path)
    img = db.import_image_file(make_png(tmp_path / "a.png"), "2026-10-30")
    db.save_entry(Entry(None, "2026-10-30", "10:00", "完成的", "done"))
    doing = db.save_entry(Entry(None, "2026-10-30", "11:00", "進行中的", "doing", ["專案A"], [img]))
    db.save_entry(Entry(None, "2026-10-30", "12:00", "等待中的", "waiting"))

    # 週一帶入週五的項目
    assert db.previous_date_with_entries("2026-11-02") == "2026-10-30"
    new_ids = db.carry_over("2026-10-30", "2026-11-02", time="09:00")
    today = db.entries_for_date("2026-11-02")
    assert [e.text for e in today] == ["進行中的", "等待中的"]
    assert today[0].tags == ["專案A"] and today[0].images == [] and today[0].carried_from == doing
    assert len(new_ids) == 2

    # 再按一次不會重複帶入
    assert db.carry_over("2026-10-30", "2026-11-02") == []
    # 原本的紀錄保留
    assert len(db.entries_for_date("2026-10-30")) == 3


def test_carry_over_skips_work_continued_elsewhere(tmp_path):
    """同一件工作已經有更新的紀錄（同一天追加進度、或從更早的紀錄追加）時，不再帶入舊的那筆。"""
    db = Database(tmp_path / "data")
    # 同一天建立、追加進度，最後一筆標成完成
    first = db.save_entry(Entry(None, "2026-11-09", "09:00", "做 X", "doing"))
    db.save_entry(Entry(None, "2026-11-09", "15:00", "做 X 完成", "done", carried_from=first))
    # 同一天追加但還沒完成：只帶入最新那筆
    other = db.save_entry(Entry(None, "2026-11-09", "10:00", "做 Z", "doing"))
    db.save_entry(Entry(None, "2026-11-09", "16:00", "做 Z 下午", "waiting", carried_from=other))
    assert [e.text for e in db.carry_over_candidates("2026-11-09", "2026-11-10")] == ["做 Z 下午"]

    # 從比較舊的紀錄追加並標成完成，中間那天的紀錄也不再帶入
    a = db.save_entry(Entry(None, "2026-11-01", "09:00", "做 Y", "doing"))
    db.save_entry(Entry(None, "2026-11-02", "09:00", "做 Y 第二天", "doing", carried_from=a))
    db.save_entry(Entry(None, "2026-11-03", "09:00", "做 Y 完成", "done", carried_from=a))
    assert db.carry_over_candidates("2026-11-02", "2026-11-03") == []

    # 刪掉新的那筆，舊的又會被帶入
    b = db.save_entry(Entry(None, "2026-11-05", "09:00", "做 W", "doing"))
    newer = db.save_entry(Entry(None, "2026-11-05", "10:00", "做 W 完成", "done", carried_from=b))
    db.delete_entry(newer)
    assert [e.text for e in db.carry_over_candidates("2026-11-05", "2026-11-06")] == ["做 W"]

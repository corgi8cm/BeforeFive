"""標準測試資料：tests/golden/<案例>/bundle.json 匯入後，產生的 report.md 必須和 expected_report.md 完全相同。

手機版（Dart）會用同一份資料跑同樣的檢查，確保兩邊格式一致。
"""
import json
from pathlib import Path

import pytest
from PIL import Image as PILImage

from reporttool.db import Database
from reporttool.report import generate_report
from reporttool.sync import import_bundle, snapshot

GOLDEN = Path(__file__).parent / "golden"
INSTRUCTIONS = (GOLDEN / "instructions.md").read_text(encoding="utf-8")
CASES = sorted(p.name for p in GOLDEN.iterdir() if (p / "bundle.json").exists())


@pytest.mark.parametrize("case", CASES)
def test_report_matches_golden(case, tmp_path):
    db = Database(tmp_path / "data")
    import_bundle(db, GOLDEN / case)
    folder = generate_report(db, "2026-11-02", tmp_path / "out", INSTRUCTIONS)
    expected = (GOLDEN / case / "expected_report.md").read_text(encoding="utf-8")
    assert (folder / "report.md").read_text(encoding="utf-8") == expected


def test_basic_images(tmp_path):
    db = Database(tmp_path / "data")
    import_bundle(db, GOLDEN / "basic")
    folder = generate_report(db, "2026-11-02", tmp_path / "out", INSTRUCTIONS)
    images = sorted(p.name for p in folder.iterdir() if p.name != "report.md")
    assert images == ["01_修登入頁timeout問題.png"]  # 沒勾給 AI 與遺失的圖不輸出
    with PILImage.open(folder / images[0]) as im:
        assert im.size == (1600, 800)


@pytest.mark.parametrize("case", CASES)
def test_snapshot_round_trip(case, tmp_path):
    """匯入再匯出，紀錄內容要和原本的 bundle 一模一樣（含 tombstone 與 carried_from）。"""
    db = Database(tmp_path / "data")
    import_bundle(db, GOLDEN / case)
    original = json.loads((GOLDEN / case / "bundle.json").read_text(encoding="utf-8"))
    assert snapshot(db)["entries"] == original["entries"]

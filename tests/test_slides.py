from PIL import Image as PILImage
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from reporttool.db import Database, Entry
from reporttool.slides import export_slides, order_by_category

DATE = "2026-11-02"


def slide_text(slide) -> str:
    return "\n".join(s.text_frame.text for s in slide.shapes if s.has_text_frame)


def pictures(slide):
    return [s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]


def test_order_by_category_tree():
    """依分類樹的順序（上層的紀錄在子分類之前），未分類最後；同一層依優先順序、再依標題，tag 與狀態不影響。"""
    def e(text, category_id, priority=3, tags=(), status="done"):
        return Entry(None, DATE, "00:00", text, status, list(tags), category_id=category_id, priority=priority), []

    dev, proj, meeting = 1, 2, 3  # 樹狀順序：開發、開發 › 專案A、會議
    items = [e("z", None), e("b 專案", proj, tags=["A"]), e("a 專案", proj, tags=["B"]), e("週會", meeting),
             e("急件", proj, priority=1, status="waiting"), e("開發雜事", dev)]
    assert [it[0].text for it in order_by_category(items, [dev, proj, meeting])] == [
        "開發雜事", "急件", "a 專案", "b 專案", "週會", "z"]


def test_export_slides(tmp_path):
    db = Database(tmp_path / "data")
    PILImage.new("RGB", (3200, 1000), "blue").save(tmp_path / "a.png")
    shown = db.import_image_file(tmp_path / "a.png", DATE)
    hidden = db.import_image_file(tmp_path / "a.png", DATE)
    hidden.include = False

    db.save_entry(Entry(None, DATE, "10:20", "修登入頁 timeout 問題\n已找到原因", "doing", ["專案A"], [shown, hidden]))
    db.save_entry(Entry(None, DATE, "11:00", "等待 DBA 開權限", "waiting", ["專案B"]))
    db.save_entry(Entry(None, DATE, "14:05", "timeout 修好，已送 PR", "done", ["專案A"]))

    path = export_slides(db, DATE, tmp_path / "out")
    assert path == tmp_path / "out" / f"report_{DATE}" / "report.pptx"

    slides = list(Presentation(str(path)).slides)
    assert len(slides) == 4  # 封面 + 3 則
    assert "工作回報" in slide_text(slides[0]) and "完成 1" in slide_text(slides[0])

    # 依 tag 分組：專案A（完成 → 進行中）、專案B
    assert "timeout 修好" in slide_text(slides[1])
    assert "修登入頁" in slide_text(slides[2])
    assert "等待 DBA" in slide_text(slides[3])

    login = slides[2]
    assert len(pictures(login)) == 1  # 沒勾給 AI 的圖不放
    assert "附圖2：未提供" in slide_text(login)
    assert "• 已找到原因" in slide_text(login)
    assert "附圖1：01_修登入頁timeout問題.png" in login.notes_slide.notes_text_frame.text


def test_export_uses_template(tmp_path):
    template = tmp_path / "template.pptx"
    prs = Presentation()  # 預設 4:3
    prs.save(template)
    db = Database(tmp_path / "data")
    db.save_entry(Entry(None, DATE, "10:00", "a", "done"))

    out = Presentation(str(export_slides(db, DATE, tmp_path / "out", template)))
    assert out.slide_width == prs.slide_width and len(out.slides) == 2

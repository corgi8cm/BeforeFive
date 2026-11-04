from PIL import Image as PILImage
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from reporttool.db import Database, Entry
from reporttool.slides import export_slides, order_by_tag

DATE = "2026-11-02"


def slide_text(slide) -> str:
    return "\n".join(s.text_frame.text for s in slide.shapes if s.has_text_frame)


def pictures(slide):
    return [s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]


def test_order_by_tag():
    def e(text, status, tags):
        return Entry(None, DATE, "00:00", text, status, tags), []

    items = [e("a", "waiting", ["A"]), e("b", "done", []), e("c", "done", ["B"]), e("d", "done", ["A", "B"])]
    assert [it[0].text for it in order_by_tag(items)] == ["d", "a", "c", "b"]


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


def test_order_by_category():
    from reporttool.slides import order_by_category

    def e(text, category, tags=()):
        return Entry(None, DATE, "00:00", text, "done", list(tags), category=category), []

    items = [e("a", ""), e("b", "會議"), e("c", "開發", ["B"]), e("d", "開發", ["A"]), e("f", "開發", ["B"])]
    assert [it[0].text for it in order_by_category(items, ["開發", "會議"])] == ["c", "f", "d", "b", "a"]

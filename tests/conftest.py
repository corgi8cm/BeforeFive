"""所有測試共用的設定。"""
import pytest

TODAY = "2026-11-02"
NOW = "17:00"


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    """輸入視窗把「今天」當成 2026-11-02 17:00：測試大多用這天的資料，時段規則才會一致。"""
    try:
        from reporttool.ui import entry_dialog
    except ImportError:  # 沒有 PySide6 時只跑非 GUI 的測試
        return
    monkeypatch.setattr(entry_dialog, "now_parts", lambda: (TODAY, NOW))


@pytest.fixture(autouse=True)
def no_modal_warning(monkeypatch):
    """沒有預期的警告框會卡住整個測試：直接讓它失敗。需要時測試自己再換掉 QMessageBox.warning。"""
    try:
        from PySide6.QtWidgets import QMessageBox
    except ImportError:
        return

    def fail(_parent, title, text, *a, **k):
        raise AssertionError(f"跳出了警告框：{title}：{text}")

    monkeypatch.setattr(QMessageBox, "warning", fail)

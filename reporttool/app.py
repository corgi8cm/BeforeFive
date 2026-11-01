"""程式進入點：單一實例、系統匣、全域快捷鍵。"""
import getpass
import sys

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .config import load_settings
from .db import Database
from .hotkey import GlobalHotkey
from .ui.main_window import MainWindow


class HotkeyBridge(QObject):
    """把背景執行緒的快捷鍵事件轉成主執行緒的 signal。"""
    pressed = Signal()


def make_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#2f6fd6"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor("white"))
    p.setFont(QFont("Microsoft JhengHei", 30, QFont.Bold))
    p.drawText(pix.rect(), Qt.AlignCenter, "報")
    p.end()
    return QIcon(pix)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ReportTool")
    app.setQuitOnLastWindowClosed(False)

    # 已經在執行就叫出現有視窗
    server_name = f"ReportTool-{getpass.getuser()}"
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    settings = load_settings()
    db = Database(settings.data_dir)
    icon = make_icon()
    app.setWindowIcon(icon)

    window = MainWindow(db, settings)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), window.show_and_raise()))

    tray = QSystemTrayIcon(icon)
    tray.setToolTip(f"工作回報工具（{settings.hotkey} 快速記錄）")
    menu = QMenu()
    for text, slot in (("快速記錄", window.open_quick_entry), ("開啟主視窗", window.show_and_raise),
                       ("工作回顧（今天）", lambda: (window.go_today(), window.review()))):
        action = QAction(text, menu)
        action.triggered.connect(slot)
        menu.addAction(action)
    menu.addSeparator()
    quit_action = QAction("結束", menu)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: window.show_and_raise() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    window.tray = tray

    bridge = HotkeyBridge()
    bridge.pressed.connect(window.open_quick_entry)
    hotkey = GlobalHotkey(settings.hotkey, bridge.pressed.emit)
    if hotkey.start():
        window.statusBar().showMessage(f"全域快捷鍵 {settings.hotkey} 已啟用", 5000)
    else:
        QMessageBox.warning(window, "快捷鍵", f"{hotkey.error}\n\n可以到 config/settings.json 修改 hotkey 後重開程式。")

    def quit_app():
        window.quitting = True
        hotkey.stop()
        tray.hide()
        db.close()
        app.quit()

    quit_action.triggered.connect(quit_app)

    window.show()
    return app.exec()

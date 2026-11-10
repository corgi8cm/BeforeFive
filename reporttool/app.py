"""程式進入點：單一實例、全域快捷鍵。關掉主視窗就結束程式，不會留在背景。"""
import getpass
import sys

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
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
    app.setApplicationName("BeforeFive")
    app.setApplicationVersion(__version__)

    # 已經在執行就叫出現有視窗
    server_name = f"ReportTool-{getpass.getuser()}"  # 改名前就用這個名稱，維持不變
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

    bridge = HotkeyBridge()
    bridge.pressed.connect(window.open_quick_entry)
    hotkey = GlobalHotkey(settings.hotkey, bridge.pressed.emit)
    if hotkey.start():
        window.statusBar().showMessage(f"全域快捷鍵 {settings.hotkey} 已啟用", 5000)
    else:
        QMessageBox.warning(window, "快捷鍵", f"{hotkey.error}\n\n可以到 config/settings.json 修改 hotkey 後重開程式。")

    def cleanup():
        hotkey.stop()
        db.close()

    app.aboutToQuit.connect(cleanup)

    window.show()
    return app.exec()

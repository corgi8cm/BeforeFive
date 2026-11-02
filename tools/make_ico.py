"""把系統匣圖示存成 .ico，給 PyInstaller 當 exe 圖示。用法：python tools/make_ico.py 輸出路徑"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

from reporttool.app import make_icon

app = QApplication([])
buf = QBuffer()
buf.open(QIODevice.WriteOnly)
make_icon().pixmap(256, 256).save(buf, "PNG")
png = Path(sys.argv[1]).with_suffix(".png")
png.write_bytes(bytes(buf.data()))
Image.open(png).save(sys.argv[1], sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
png.unlink()

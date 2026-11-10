"""Write the PyInstaller version resource for the exe. Usage: python tools/make_version_file.py OUTPUT_PATH"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reporttool import __version__


def version_info(version: str) -> str:
    numbers = tuple(int(n) for n in version.split(".")) + (0,)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', '下班前 BeforeFive'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'BeforeFive'),
      StringStruct('OriginalFilename', 'BeforeFive.exe'),
      StringStruct('ProductName', '下班前 BeforeFive'),
      StringStruct('ProductVersion', '{version}'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Corgi Tsai. MIT License.')])]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
"""


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(version_info(__version__), encoding="utf-8")

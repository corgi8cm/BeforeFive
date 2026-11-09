"""讀取 config/settings.json 與 config/instructions.md。"""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# 打包成 exe 時，config/ 與 WorkReports/ 放在 exe 旁邊
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

DEFAULT_SETTINGS = {
    "hotkey": "Ctrl+Alt+R",
    "data_dir": "",  # 空白 = %LOCALAPPDATA%\ReportTool\data
    "output_dir": "",  # 空白 = 專案底下的 WorkReports
    "max_image_side": 1600,
    "show_progress": True,  # 主視窗是否顯示分類進度（主視窗的「顯示進度」勾選框）
}

DEFAULT_INSTRUCTIONS = """\
- 依紀錄中的 [分類：…] 分層整理：第一層分類當大標題（例如 *【開發】*），底下的子分類依序當小標題（例如 *專案A › 登入頁*），沒有分類的放在最後的「*【未分類】*」
- #tag 只是補充標籤，可以在描述中帶出，但不要用來分組
- 同一層的項目依 [優先N] 排列（1 最前），相同時依標題；每項開頭標上狀態符號：✅ 完成、👀 待確認、🔄 進行中、⏳ 等待中、📝 待辦、🚫 取消
- 有「## 分類進度」時，在對應的分類標題後面標上進度（例如 *專案A（40%）*）
- 標有（延續自 MM-DD）的項目是從那天起持續進行的工作，可以在描述中帶出
- 標有 ⇄ 的是和前一次紀錄相比的變化（前：／後：是改動的內容），用來說明這次的進展；「沒有變更」的項目簡短帶過即可
- 只用紀錄和圖片中的內容，不要補充或編造
- 參考圖片內容讓描述更具體，但保留（附圖N）標記
- 用 Slack mrkdwn 格式，語氣簡潔、專業
"""

_LINES = DEFAULT_INSTRUCTIONS.splitlines()
CATEGORY_LINE, TAG_LINE, ORDER_LINE, PROGRESS_LINE, CONTINUED_LINE, CHANGES_LINE = _LINES[:6]
# 舊版預設的那幾行要換成什麼。instructions.md 只在第一次執行時建立，程式更新後要自動換成新版；
# 使用者自己改過的行對不上，就不會動。
OLD_LINES = {
    # 1.0.0：分類只有一層、依 tag 分組
    "- 先依紀錄中的 [分類：X] 分成大段，用分類名稱當標題（例如 *【開發】*），沒有分類的放在最後的「*【未分類】*」":
        [CATEGORY_LINE],
    "- 每個分類內再依 tag 分組，用 tag 當小標題（例如 *#專案A*），相同 tag 的項目放在同一組；一筆有多個 tag 時以第一個為準，"
    "沒有 tag 的放在該分類最後": [TAG_LINE],
    "- 每組內用條列，依「✅ 完成」「👀 待確認」「🔄 進行中」「⏳ 等待中」「📝 待辦」「🚫 取消」的順序排列，每項開頭標上對應的狀態符號":
        [ORDER_LINE, PROGRESS_LINE],
    # 1.0.0 之前：三種狀態
    "- 每組內用條列，依「✅ 完成」「🔄 進行中」「⛔ 卡住」的順序排列，每項開頭標上對應的狀態符號": [ORDER_LINE, PROGRESS_LINE],
}


def upgrade_instructions(text: str) -> str:
    """只改舊版預設的那幾行，使用者自己寫的內容不動。"""
    lines = []
    for line in text.splitlines():
        lines += OLD_LINES.get(line.strip(), [line])
    if "⇄" not in text and CONTINUED_LINE in lines:
        lines.insert(lines.index(CONTINUED_LINE) + 1, CHANGES_LINE)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


@dataclass
class Settings:
    hotkey: str
    data_dir: Path
    output_dir: Path
    max_image_side: int
    config_dir: Path
    show_progress: bool = True

    def save(self, key: str, value):
        """把一個設定寫回 settings.json（其他內容不動），同時更新這個物件。"""
        path = self.config_dir / "settings.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = dict(DEFAULT_SETTINGS)
        raw[key] = value
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        setattr(self, key, value)

    @property
    def instructions_path(self) -> Path:
        return self.config_dir / "instructions.md"

    @property
    def ppt_template_path(self) -> Path:
        """放了這個檔案就用它當 PPT 模板，沒有就用內建樣式。"""
        return self.config_dir / "ppt_template.pptx"


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / "ReportTool" / "data"


def load_settings(config_dir: Path = CONFIG_DIR) -> Settings:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "settings.json"
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_SETTINGS, ensure_ascii=False, indent=2), encoding="utf-8")
    raw = {**DEFAULT_SETTINGS, **json.loads(path.read_text(encoding="utf-8"))}

    instructions = config_dir / "instructions.md"
    if not instructions.exists():
        instructions.write_text(DEFAULT_INSTRUCTIONS, encoding="utf-8")
    else:
        text = instructions.read_text(encoding="utf-8")
        if upgrade_instructions(text) != text:
            instructions.write_text(upgrade_instructions(text), encoding="utf-8")

    return Settings(
        hotkey=raw["hotkey"],
        data_dir=Path(os.path.expandvars(raw["data_dir"])) if raw["data_dir"] else default_data_dir(),
        output_dir=Path(os.path.expandvars(raw["output_dir"])) if raw["output_dir"] else ROOT / "WorkReports",
        max_image_side=int(raw["max_image_side"]),
        config_dir=config_dir,
        show_progress=bool(raw["show_progress"]),
    )


def load_instructions(settings: Settings) -> str:
    """每次產生回報時重新讀取，修改後不用重開程式。"""
    try:
        return settings.instructions_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DEFAULT_INSTRUCTIONS

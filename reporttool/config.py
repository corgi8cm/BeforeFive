"""讀取 config/settings.json 與 config/instructions.md。"""
import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

DEFAULT_SETTINGS = {
    "hotkey": "Ctrl+Alt+R",
    "data_dir": "",  # 空白 = %LOCALAPPDATA%\ReportTool\data
    "output_dir": "",  # 空白 = 專案底下的 WorkReports
    "max_image_side": 1600,
}

DEFAULT_INSTRUCTIONS = """\
- 依 tag 分組，用 tag 當小標題（例如 *#專案A*），相同 tag 的項目全部放在同一組；一筆有多個 tag 時以第一個為準，沒有 tag 的放在最後的「*其他*」組
- 每組內用條列，依「✅ 完成」「🔄 進行中」「⛔ 卡住」的順序排列，每項開頭標上對應的狀態符號
- 只用紀錄和圖片中的內容，不要補充或編造
- 參考圖片內容讓描述更具體，但保留（附圖N）標記
- 用 Slack mrkdwn 格式，語氣簡潔、專業
"""


@dataclass
class Settings:
    hotkey: str
    data_dir: Path
    output_dir: Path
    max_image_side: int
    config_dir: Path

    @property
    def instructions_path(self) -> Path:
        return self.config_dir / "instructions.md"


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

    return Settings(
        hotkey=raw["hotkey"],
        data_dir=Path(os.path.expandvars(raw["data_dir"])) if raw["data_dir"] else default_data_dir(),
        output_dir=Path(os.path.expandvars(raw["output_dir"])) if raw["output_dir"] else ROOT / "WorkReports",
        max_image_side=int(raw["max_image_side"]),
        config_dir=config_dir,
    )


def load_instructions(settings: Settings) -> str:
    """每次產生回報時重新讀取，修改後不用重開程式。"""
    try:
        return settings.instructions_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DEFAULT_INSTRUCTIONS

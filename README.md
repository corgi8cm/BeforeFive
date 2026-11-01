# ReportTool 工作回報工具

工作中隨手記錄，下班前一鍵整理成 `report_YYYY-MM-DD/` 資料夾（含 `report.md` 與附圖），丟給網頁版 Claude / Gemini 寫成 Slack 回報。

所有資料只存在本機，不呼叫任何外部 API。

## 安裝與啟動（Windows）

1. 安裝 Python 3.10 以上
2. 雙擊 `setup.bat`：在 `%LOCALAPPDATA%\ReportTool\venv` 建立虛擬環境並安裝套件（放在 OneDrive 外，避免同步數百 MB 的套件）
3. 雙擊 `run.bat` 啟動（不會出現主控台視窗）

程式會常駐在系統匣，關掉主視窗只是縮到系統匣；要結束請在系統匣圖示按右鍵 →「結束」。重複執行 `run.bat` 只會叫出現有視窗。

**開機自動啟動**：按 `Win+R` 輸入 `shell:startup`，把 `run.bat` 的捷徑放進去。

## 使用方式

| 動作 | 方式 |
|---|---|
| 快速記錄 | 任何地方按 **Ctrl+Alt+R**（可在設定檔修改） |
| 貼上截圖 | 在文字框按 `Ctrl+V`（例如先用 `Win+Shift+S` 截圖） |
| 加入圖片檔 | 拖曳到視窗，或按「附加圖片…」 |
| 切換狀態 | `Alt+1` 完成 / `Alt+2` 進行中 / `Alt+3` 卡住 |
| 加 tag | 輸入 `#專案A #bug`，會自動補完；下方常用 tag 點一下即可加入或移除 |
| 儲存 / 取消 | `Ctrl+Enter` / `Esc` |
| 編輯、刪除 | 主視窗雙擊紀錄，或選取後按「編輯」「刪除」 |
| 圖片不給 AI | 取消圖片下方的「給 AI」勾選 |
| 帶入未完成項目 | 主視窗按「帶入未完成項目」：把上一個有紀錄的日子（週一會帶入週五）中「進行中／卡住」的項目複製到今天；不帶圖片，重複按也不會重複帶入 |
| 工作回顧 | 主視窗按「📋 工作回顧」，或系統匣選單 |

## 工作回顧產出

```
文件\WorkReports\report_2026-11-02\
├─ 01_修登入頁timeout問題.png   ← 長邊縮到 1600px
├─ 02_timeout修好，已送PR.png
└─ report.md
```

- 圖片依紀錄時間排序編號，檔名取紀錄內容的第一行
- 沒勾「給 AI」的圖片不會複製，md 中寫成「（附圖3：未提供）」
- 重新產生時只會覆蓋 `report.md` 和編號圖片，資料夾裡其他檔案不受影響

## 設定

都在 `config/` 資料夾（主視窗有「開啟設定資料夾」「編輯 AI 指示」按鈕）：

- `instructions.md`：report.md 中「## 指示」的內容。每次產生時重新讀取，改完不用重開程式
- `settings.json`：
  - `hotkey`：全域快捷鍵，例如 `"Ctrl+Alt+R"`、`"Ctrl+Shift+Space"`、`"Win+F9"`
  - `data_dir`：資料存放位置，空白表示 `%LOCALAPPDATA%\ReportTool\data`
  - `output_dir`：回報資料夾位置，空白表示 `文件\WorkReports`
  - `max_image_side`：圖片長邊上限，預設 1600

修改 `settings.json` 後要重開程式。

## 資料儲存

`data_dir` 裡有：

- `reporttool.db`：SQLite，包含 `entries`（紀錄）、`entry_tags`、`tags`（用過的 tag）、`images`
- `images/YYYY-MM-DD/`：原始圖片

要備份就複製整個 `data_dir`。歷史紀錄會一直保留；`Database.entries_between(start, end)` 可以直接拿來做週報。

## 開發

```
%LOCALAPPDATA%\ReportTool\venv\Scripts\python.exe -m pytest
```

```
main.py                     進入點
reporttool/
  app.py                    系統匣、全域快捷鍵、單一實例
  config.py                 讀 config/
  db.py                     SQLite 資料層
  report.py                 工作回顧（資料夾、縮圖、report.md）
  hotkey.py                 Windows RegisterHotKey
  ui/main_window.py         主視窗
  ui/entry_dialog.py        新增／編輯紀錄視窗
tests/
```

之後要加的功能：
- 下班前提醒：在 `app.py` 加一個 `QTimer`，時間到時呼叫 `tray.showMessage`
- 週報：新增 `weekly.py`，用 `entries_between` 撈一週的資料，產生格式類似的 md

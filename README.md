# ReportTool 工作回報工具

工作中隨手記錄，下班前一鍵整理成 `report_YYYY-MM-DD/` 資料夾（含 `report.md` 與附圖），丟給網頁版 Claude / Gemini 寫成 Slack 回報。

所有資料只存在本機，不呼叫任何外部 API。

## 安裝與啟動（Windows）

1. 安裝 Python 3.10 以上
2. 雙擊 `setup.bat`：在 `%LOCALAPPDATA%\ReportTool\venv` 建立虛擬環境並安裝套件（放在 OneDrive 外，避免同步數百 MB 的套件）
3. 雙擊 `run.bat` 啟動（不會出現主控台視窗）

關掉主視窗就會結束程式，不會留在背景執行。全域快捷鍵只在程式開著時有效（主視窗最小化也可以）。重複執行 `run.bat` 只會叫出現有視窗。

**開機自動啟動**：按 `Win+R` 輸入 `shell:startup`，把 `run.bat`（或 `ReportTool.exe`）的捷徑放進去。

### 打包成 exe

完成上面的 `setup.bat` 後，雙擊 `build.bat`，約一分鐘後產生 `dist\ReportTool.exe`（單一檔案，約 60 MB，不需要安裝 Python 就能執行）。

- 把 exe 放到任何資料夾都可以；第一次執行會在 exe 旁邊建立 `config\`，`WorkReports\` 也會產生在 exe 旁邊
- 紀錄資料一樣在 `%LOCALAPPDATA%\ReportTool\data`，和 `run.bat` 版本共用同一份
- 單一檔案的 exe 每次啟動要先解壓，大約需要幾秒鐘
- exe 和 `run.bat` 版本不要同時執行（後啟動的會只叫出先啟動的那個視窗）

## 使用方式

| 動作 | 方式 |
|---|---|
| 快速記錄 | 程式開著時，任何地方按 **Ctrl+Alt+R**（可在設定檔修改） |
| 貼上截圖 | 在文字框按 `Ctrl+V`（例如先用 `Win+Shift+S` 截圖） |
| 加入圖片檔 | 拖曳到視窗，或按「附加圖片…」 |
| 切換狀態 | `Alt+1` 完成 / `Alt+2` 進行中 / `Alt+3` 卡住 |
| 選分類 | tag 欄左邊的下拉選單（開發／會議／支援／行政／其他，可自行管理） |
| 加 tag | 輸入 `#專案A #bug`，會自動補完；下方常用 tag 點一下即可加入或移除 |
| 預設值 | 新紀錄會沿用上一筆的分類與 tag，不需要時清掉即可 |
| 儲存 / 取消 | `Ctrl+Enter` / `Esc` |
| 編輯、刪除 | 主視窗雙擊紀錄，或選取後按「編輯」「刪除」 |
| 快速改狀態 | 主視窗在紀錄上按右鍵 →「標記為完成／進行中／卡住」 |
| 追加今日進度 | 選取一筆工作（可以是之前的日子）→ 按「追加今日進度」或右鍵選單。新紀錄會串到原本那件工作，視窗上方會列出先前進度，回報中標示「（延續自 MM-DD）」 |
| 依分類篩選 | 主視窗上方的「全部分類」下拉選單 |
| 依 tag 分組 | 主視窗「依 tag 分組」打勾時，相同 tag 的紀錄放在同一組（以第一個 tag 為準，沒有 tag 的放最後）；選「全部分類」時先分分類、再分 tag。群組可以收合，取消打勾回到一般清單 |
| 管理分類與標籤 | 主視窗「管理分類與標籤」：分類可新增、改名、刪除、排序；tag 可改名、合併（改成已存在的名稱）、刪除 |
| 圖片不給 AI | 取消圖片下方的「給 AI」勾選 |
| 帶入未完成項目 | 主視窗按「帶入未完成項目」：把上一個有紀錄的日子（週一會帶入週五）中「進行中／卡住」的項目複製到今天；不帶圖片，重複按也不會重複帶入 |
| 工作回顧 | 主視窗按「📋 工作回顧」。report.md 的內容會同時複製到剪貼簿，可以直接貼到 AI 網頁 |
| 匯出 PPT | 主視窗按「📊 匯出 PPT」，產生 `report_日期/report.pptx` |

## 工作回顧產出

```
ReportTool\WorkReports\report_2026-11-02\
├─ 01_修登入頁timeout問題.png   ← 長邊縮到 1600px
├─ 02_timeout修好，已送PR.png
└─ report.md
```

- 圖片依紀錄時間排序編號，檔名取紀錄內容的第一行
- 沒勾「給 AI」的圖片不會複製，md 中寫成「（附圖3：未提供）」
- 重新產生時只會覆蓋 `report.md` 和編號圖片，資料夾裡其他檔案不受影響

## 匯出 PPT

- 第 1 頁是封面（日期、各狀態數量、當天用到的 tag），之後一則紀錄一頁
- 頁面先依分類（照「管理分類」中的順序，未分類放最後）、再依 tag 分組排列（以第一個 tag 為準，組內依 完成 → 進行中 → 卡住）
- 每頁有時間、狀態、分類、tag、延續日期、內容；有附圖時文字在左、圖片在右，標示的附圖編號與 report.md 相同
- 沒勾「給 AI」的圖片不放進投影片，只留「附圖N：未提供」文字
- 每頁的備忘稿放了原始紀錄，AI 美化時可以參考
- 想換樣式：把自己的 .pptx 存成 `config/ppt_template.pptx`，會沿用它的投影片大小與佈景主題（使用其中的「空白」版面配置）

## 設定

都在 `config/` 資料夾（主視窗有「開啟設定資料夾」「編輯 AI 指示」按鈕）：

- `instructions.md`：report.md 中「## 指示」的內容。每次產生時重新讀取，改完不用重開程式
- `settings.json`：
  - `hotkey`：全域快捷鍵，例如 `"Ctrl+Alt+R"`、`"Ctrl+Shift+Space"`、`"Win+F9"`
  - `data_dir`：資料存放位置，空白表示 `%LOCALAPPDATA%\ReportTool\data`
  - `output_dir`：回報資料夾位置，空白表示專案底下的 `WorkReports\`（已加入 .gitignore，不會上傳）
  - `max_image_side`：圖片長邊上限，預設 1600

修改 `settings.json` 後要重開程式。

## 資料儲存

`data_dir` 裡有：

- `reporttool.db`：SQLite，包含 `entries`（紀錄，`carried_from` 串起同一件工作的歷程）、`categories`、`entry_tags`、`tags`（用過的 tag）、`images`、`meta`（裝置 id）
- `images/YYYY-MM-DD/`：原始圖片

歷史紀錄會一直保留；`Database.entries_between(start, end)` 可以直接拿來做週報。

為了之後和手機版同步，每筆紀錄有 uuid 與 UTC 修改時間，刪除的紀錄會留下不含內容的 tombstone。格式規格見 [docs/data-format.md](docs/data-format.md)。

### 備份與匯入

主視窗「備份 ▾」：

- **匯出備份…**：所有紀錄（含圖片）存成一個 zip
- **從備份匯入…**：把 zip 合併進目前的資料。同一筆紀錄以較晚修改的版本為準，所以重複匯入、或在兩台電腦間互相匯入都不會產生重複資料

舊版資料庫會在第一次開啟時自動升級。

## 開發

```
%LOCALAPPDATA%\ReportTool\venv\Scripts\python.exe -m pytest
```

```
main.py                     進入點
build.bat                   打包成 dist\ReportTool.exe
tools/make_ico.py           產生 exe 圖示
reporttool/
  app.py                    全域快捷鍵、單一實例
  config.py                 讀 config/
  db.py                     SQLite 資料層（含版本升級）
  sync.py                   備份匯出／匯入與合併規則（之後手機同步共用）
  report.py                 工作回顧（資料夾、縮圖、report.md）
  slides.py                 匯出 PPT
  hotkey.py                 Windows RegisterHotKey
  ui/main_window.py         主視窗
  ui/entry_dialog.py        新增／編輯／追加進度視窗
  ui/manage_dialog.py       管理分類與標籤
tests/
  golden/                   標準測試資料：輸入 bundle 與預期的 report.md（手機版也要通過）
docs/
  data-format.md            資料包、合併規則、report.md 格式規格
  mobile-roadmap.md         手機版（Android）規劃，目前暫停
```

之後要加的功能：
- 下班前提醒：在 `app.py` 加一個 `QTimer`，時間到時跳出提醒視窗（程式要開著才會提醒）
- 週報：新增 `weekly.py`，用 `entries_between` 撈一週的資料，產生格式類似的 md

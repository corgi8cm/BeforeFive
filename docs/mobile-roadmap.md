# 手機版 Roadmap（暫停中）

> 狀態（2026-11-02）：第 0 階段已完成並合併進 `main`。第 1 階段**暫停**，等有適合的開發機再開始；目前先繼續開發 PC 版。

## 已決定的事

| 項目 | 決定 |
|---|---|
| 平台 | Android（先不做 iPhone） |
| 同步 | 手機和電腦要同步，不經過雲端 |
| 技術 | Flutter（Dart） |
| 專案位置 | 同一個 repo 的 `mobile/` 資料夾，新 branch `phase1-mobile` |
| 格式依據 | [data-format.md](data-format.md)，並通過 `tests/golden/` 的標準測試資料 |

## 桌面功能在手機上的對應

| 桌面版 | 手機版 |
|---|---|
| 全域快捷鍵 | 桌面小工具（一鍵新增）、通知列快捷按鈕 |
| 貼上／拖曳截圖 | 從相簿選、拍照、在截圖上按「分享」送進 App |
| 系統匣常駐 | 不需要 |
| 開啟回報資料夾 | 用分享選單把 md 和圖片送到 AI App、Slack 或「檔案」 |
| 複製 md 到剪貼簿 | 一樣做得到 |
| 匯出 PPT | 留在電腦版 |

## 階段

### 第 0 階段：桌面版準備 ✅ 已完成

- 紀錄有 uuid、UTC 毫秒修改時間，刪除留下 tombstone，分類以名稱辨識
- 資料庫依 `PRAGMA user_version` 升級
- `reporttool/sync.py`：snapshot／merge，匯出／匯入 zip（主視窗「備份 ▾」）
- `tests/golden/`：標準輸入與預期 report.md
- `docs/data-format.md`：格式與合併規則規格

### 第 1 階段：手機 MVP（暫停中）

1. 建立 `mobile/` Flutter 專案
2. 先寫資料層（sqflite，表格結構照桌面版）與 report.md 產生，用 `tests/golden/` 驗證逐字相同
3. 畫面：快速記錄（文字、照片、狀態、分類、tag，預設沿用上一筆）、今日清單（左右滑改狀態）、追加進度、帶入未完成項目
4. 工作回顧：產生 report.md 與縮圖，透過分享選單送出、複製到剪貼簿
5. 分類與 tag 管理
6. 從相簿或截圖「分享」進 App
7. 匯入／匯出和桌面版相同格式的備份 zip（第 2 階段前的手動同步）

預計套件：`sqflite`、`path_provider`、`image_picker`、`image`（縮圖）、`share_plus`、`receive_sharing_intent`、`uuid`。

### 第 2 階段：區網同步

- 桌面版開一個只在區網使用的同步服務，手機掃 QR code 配對
- 交換 `snapshot()`，用 `merge()` 合併（規則已在第 0 階段實作並測試）
- 圖片只傳對方沒有的

### 第 3 階段：補強

- 週報／月報
- 下班前提醒寫回顧
- App 鎖（指紋）
- iPhone 版（需 Apple 開發者帳號與 Mac 或雲端建置）

### 第 4 階段（之後再決定）

- 用 Flutter 重寫 Windows 版，統一成一份程式碼（PPT 匯出需另找做法）

## 重新開始前要準備

開發機需要：

- Flutter SDK（stable，約 2 GB）
- Android Studio（含 Android SDK、模擬器、Java；安裝後約 6～8 GB）
- 執行 `flutter doctor --android-licenses` 同意授權
- 用實機測試的話：手機開「開發人員選項」→ USB 偵錯

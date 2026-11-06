# 手機版 Roadmap

> 狀態（2026-11-06）：第 1 階段進行中，分支 `phase1-mobile`。APK 用 GitHub Actions 在雲端建置，開發用的這台電腦不安裝 Flutter／Android Studio。

## 已決定的事

| 項目 | 決定 |
|---|---|
| 平台 | Android（先不做 iPhone） |
| 同步 | 手機和電腦要同步，不經過雲端 |
| 技術 | Flutter（Dart） |
| 專案位置 | 同一個 repo 的 `mobile/` 資料夾，分支 `phase1-mobile` |
| 建置 | GitHub Actions（`.github/workflows/mobile.yml`）：檢查、跑測試、產生 APK；推送後到 Actions 頁面下載 APK 裝到手機測試 |
| 格式依據 | [data-format.md](data-format.md)，並通過 `tests/golden/` 的標準測試資料 |
| 版本號 | 手機版自己的版本號，寫在 `mobile/pubspec.yaml`，從 0.1.0 開始；做到和桌面版功能對齊時升到 1.0.0 |

## 桌面功能在手機上的對應

| 桌面版 | 手機版 |
|---|---|
| 全域快捷鍵 | 桌面小工具（一鍵新增）、通知列快捷按鈕 |
| 貼上／拖曳截圖 | 從相簿選、拍照、在截圖上按「分享」送進 App |
| 系統匣常駐 | 不需要 |
| 開啟回報資料夾 | 用分享選單把 md 和圖片送到 AI App、Slack 或「檔案」 |
| 複製 md 到剪貼簿 | 一樣做得到 |
| 延續項目前後比較 | 一樣做得到（左右並排改成上下排列） |
| 範圍整理 | 一樣做得到 |
| 匯出 PPT | 留在電腦版 |

## 階段

### 第 0 階段：桌面版準備 ✅ 已完成

- 紀錄有 uuid、UTC 毫秒修改時間，刪除留下 tombstone，分類以名稱辨識
- 資料庫依 `PRAGMA user_version` 升級
- `reporttool/sync.py`：snapshot／merge，匯出／匯入 zip（主視窗「備份 ▾」）
- `tests/golden/`：標準輸入與預期 report.md
- `docs/data-format.md`：格式與合併規則規格（已涵蓋標題、優先順序、六種狀態、⇄ 變更行、範圍整理）

### 第 1 階段：手機 MVP（進行中）

| 步驟 | 內容 | 完成後可以 |
|---|---|---|
| 1-1 建置環境 | `mobile/` Flutter 專案骨架；GitHub Actions 自動檢查、測試、產生 APK | 下載空殼 APK 裝到手機，確認流程走得通 |
| 1-2 資料層＋匯入備份 | sqflite 資料庫，欄位和桌面版相同；讀寫桌面版的備份 zip | 把電腦的備份匯入手機，用真實資料測試 |
| 1-3 report.md 產生 | 和桌面版逐字相同（狀態、優先、延續自、⇄ 變更行、附圖引用寫法），用 `tests/golden/` 驗證 | 手機產生的回報和電腦版一樣 |
| 1-4 核心畫面 | 快速記錄（標題、內容、拍照／相簿、狀態、優先、分類、tag，沿用上一筆）、今日清單（分類與 tag 分組、滑動或長按改狀態）、追加今日進度、帶入未完成項目 | 每天在手機上記錄 |
| 1-5 工作回顧 | report.md 與縮圖，分享選單送出、複製到剪貼簿；延續項目前後比較；範圍整理 | 在手機上完成回報 |
| 1-6 補齊並發布 | 分類與 tag 管理；從相簿或截圖「分享」進 App；正式簽章；GitHub Releases 發布 APK | 安裝正式版，之後更新不用先移除 |

預計套件：`sqflite`、`path_provider`、`image_picker`、`image`（縮圖）、`archive`（zip）、`share_plus`、`receive_sharing_intent`、`uuid`。

注意：1-6 設定正式簽章之前，每次雲端建置的 APK 簽章都不同，安裝新版前要先移除舊版（資料會一起清掉，可先用備份 zip 匯出）。

### 第 2 階段：區網同步

- 桌面版開一個只在區網使用的同步服務，手機掃 QR code 配對
- 交換 `snapshot()`，用 `merge()` 合併（規則已在第 0 階段實作並測試）
- 圖片只傳對方沒有的

### 第 3 階段：補強

- 桌面小工具（一鍵新增）
- 下班前提醒寫回顧
- App 鎖（指紋）
- iPhone 版（需 Apple 開發者帳號與 Mac 或雲端建置）

### 第 4 階段（之後再決定）

- 用 Flutter 重寫 Windows 版，統一成一份程式碼（PPT 匯出需另找做法）

## 安裝測試版 APK

1. 到 GitHub repo 的 **Actions** →「Mobile」→ 最新一次成功的執行 → 最下方 **Artifacts** 下載 `reporttool-apk`（zip，裡面是 `app-release.apk`）
2. 把 APK 傳到手機（例如 Google Drive、USB、寄給自己）
3. 手機點 APK 安裝；第一次會要求允許「安裝不明來源的應用程式」

## 如果之後要在本機開發

- Flutter SDK（stable，約 2 GB）
- Android Studio（含 Android SDK、模擬器、Java；安裝後約 6～8 GB）
- 執行 `flutter doctor --android-licenses` 同意授權
- `mobile/` 目前沒有提交 `android/` 資料夾，建置時用 `flutter create` 產生；在本機先執行 `flutter create --platforms=android --org io.github.corgi8cm --project-name reporttool_mobile .`

# 資料格式規格

桌面版（Python）與之後的手機版（Flutter／Dart）共用這份規格。兩邊的實作都要通過 `tests/golden/` 的標準測試資料。

## 1. 時間與識別

| 項目 | 格式 | 例子 |
|---|---|---|
| 日期 `date` | `YYYY-MM-DD`（使用者當地日期） | `2026-11-02` |
| 時間 `time` | `HH:MM`（使用者當地時間） | `14:05` |
| 時間戳記 `created_at`／`updated_at`／`deleted_at` | UTC，固定到毫秒，結尾 `Z` | `2026-11-02T06:05:00.123Z` |
| 紀錄 `uuid` | UUID v4 字串 | `33333333-3333-4333-8333-333333333333` |
| 裝置 `device_id` | UUID v4，每個資料庫建立時產生一次 | |

時間戳記格式固定，所以可以直接用字串比較大小。Dart 的 `toIso8601String()` 可能輸出微秒，必須自行格式化成三位毫秒。

## 2. 狀態

| 值 | 顯示 |
|---|---|
| `done` | 完成 |
| `doing` | 進行中 |
| `blocked` | 卡住 |

## 3. 資料包（bundle）

匯出、匯入、之後的區網同步都用同一種結構。可以是 zip 檔，也可以是資料夾：

```
bundle.json
images/2026-11-02/<檔名>.png
```

`bundle.json`：

```json
{
  "format": "reporttool-bundle",
  "version": 1,
  "device_id": "…",
  "exported_at": "2026-11-02T09:00:00.000Z",
  "categories": [
    {"name": "開發", "position": 0, "updated_at": "…", "deleted_at": null}
  ],
  "entries": [
    {
      "uuid": "…",
      "date": "2026-11-02",
      "time": "10:20",
      "text": "第一行是標題\n第二行起是補充",
      "status": "doing",
      "category": "開發",
      "tags": ["專案A"],
      "images": [{"path": "images/2026-11-02/<hex>.png", "original_name": "截圖", "include": true}],
      "carried_from": "<另一筆紀錄的 uuid 或 null>",
      "created_at": "…",
      "updated_at": "…",
      "deleted_at": null
    }
  ]
}
```

欄位說明：

- `category`：分類名稱，`null` 表示未分類。**分類用名稱辨識**（和 tag 一樣），兩台裝置各自建立同名分類時會自然合併。
- `tags`：依使用者輸入的順序排列，不含 `#`。排在第一個的 tag 會用來分組。
- `images`：依顯示順序排列。
  - `path` 必須符合 `images/YYYY-MM-DD/[A-Za-z0-9_-]+.副檔名`，檔名用隨機 hex，所以跨裝置不會撞名。
  - `include: false` 表示「不給 AI」：回報時不輸出這張圖，只標示「附圖N：未提供」。
- `carried_from`：這筆紀錄是從哪一筆延續過來的（帶入未完成項目或追加進度），用來串起同一件工作的歷程。
- `deleted_at`：不是 `null` 時，這筆是 **tombstone**（已刪除的紀錄）。這時 `text` 是空字串，`category` 是 `null`，`tags` 和 `images` 都是空陣列。
- 分類也有 tombstone。改名等於「舊名稱 tombstone + 新名稱 + 紀錄改用新名稱」。

匯入前要先整份檢查格式，任何一項不合就整份不匯入。

## 4. 合併規則

把另一台裝置的 bundle 合併進本機：

1. 先合併分類，再合併紀錄，最後接上 `carried_from`。
2. 用 `uuid`（紀錄）或 `name`（分類）對應本機資料。本機沒有的就新增，tombstone 也要新增，這樣之後不會被舊資料救回來。
3. 兩邊都有時，比較 `updated_at`，較新的勝出。完全相同時：
   1. 刪除優先；
   2. 都沒刪或都刪了：把兩邊的紀錄都轉成 JSON（key 排序、不跳脫中文），字串比較大的勝出。

   這樣兩台裝置不論合併順序如何，都會得到相同結果。
4. 紀錄勝出時，tag 和圖片清單整個以勝出的版本為準。本機多出來的圖片檔要刪除。本機沒有的圖片從 bundle 複製過來；bundle 裡也沒有檔案時，照樣記錄，回報會標示「檔案遺失」。
5. 紀錄用到的分類本機沒有時就建立。如果本機已經刪除了這個分類，就把紀錄當作未分類。
6. 對方刪除分類時，本機屬於這個分類的紀錄改成未分類，但**不更新**這些紀錄的 `updated_at`，以免蓋掉對方較新的版本。
7. 使用者在本機改 tag 名稱、刪除 tag、改分類名稱、刪除分類時，受影響的紀錄都要更新 `updated_at`。

## 5. 回報（report.md）

產生某一天的回報：

1. 取當天沒被刪除的紀錄，依 `time` 排序，相同時間再依 `created_at`。
2. 圖片依紀錄順序從 1 開始編號，**包含**沒勾「給 AI」的圖。
3. 輸出的圖檔名稱：`{編號兩位數}_{描述}{副檔名}`。
   - 描述取 `text` 去掉頭尾空白後的第一行，移除所有空白、控制字元與 `\/:*?"<>|`，再去掉頭尾的 `.` 和空格，取前 20 個字；結果是空的就用 `image`。
   - 副檔名：原圖是 `.jpg`／`.jpeg` 時輸出 `.jpg`，其他一律 `.png`。
   - 長邊超過設定值（預設 1600）時等比例縮小，不放大。
4. 每筆紀錄一行（多行內容從第二行起縮排兩格）：

```
- [HH:MM][狀態][分類：X][#tag1 #tag2] 內容（延續自 MM-DD）（附圖1：01_xxx.png、附圖2：未提供）
```

   - 沒有分類就省略 `[分類：X]`，沒有 tag 就省略 tag 那一段。
   - 「延續自」的日期是沿著 `carried_from` 一路往前找到的**最早**那筆紀錄的日期。
   - 圖片標示：沒勾「給 AI」→`附圖N：未提供`；檔案不存在→`附圖N：檔案遺失`；其他→`附圖N：檔名`。

5. 整份檔案：

```
# 工作回報整理請求（YYYY-MM-DD）

## 指示
{config/instructions.md 的內容，去掉頭尾空白}

## 今日工作紀錄
{每筆紀錄一行；沒有紀錄時寫「（今天沒有紀錄）」}
```

完整的例子見 `tests/golden/basic/`：`bundle.json` 是輸入，`expected_report.md` 是用 `tests/golden/instructions.md` 當指示時應該產生的結果。

## 6. 標準測試資料（golden）

`tests/golden/<案例>/` 每個案例要通過兩項檢查：

1. 匯入 `bundle.json` 後產生 2026-11-02 的回報，內容必須和 `expected_report.md` **逐字相同**。
2. 匯入後再匯出，`entries` 必須和原本的 `bundle.json` 完全相同。

修改回報格式時，要同時更新 `expected_report.md` 與這份文件。

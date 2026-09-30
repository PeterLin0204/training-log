# 訓練摘要腳本 — 階段 1 規格

## 這個腳本要做什麼

從 Intervals.icu 拉訓練與健康資料，套上 `plan.json` 的週期結構，
產出一份**可直接貼進聊天視窗的純文字摘要**。

不做網頁、不做資料庫、不做 AI 對話。
Intervals.icu 就是資料庫；這個腳本只是它的翻譯層。

成功的定義：`python digest.py --weeks 4` 印出一份 200–400 行的文字，
貼進對話後，教練端能看到完整的訓練趨勢與每一趟間歇的表現。

---

## 環境

- Python 3.11+
- 依賴盡量少：`requests` 就夠，不要引入框架
- 憑證從環境變數讀，**絕不寫進原始碼或 commit**

```
ICU_ATHLETE_ID=i123456
ICU_API_KEY=xxxxxxxx
```

用 `.env` + `python-dotenv`，並把 `.env` 加進 `.gitignore`。

---

## Intervals.icu API

Base URL：`https://intervals.icu/api/v1`

**認證**：HTTP Basic Auth，username 固定字串 `API_KEY`，password 為個人 API key。
（另一種寫法是 header `Authorization: ApiKey {ATHLETE_ID}:{API_KEY}`，兩種擇一即可。）

API key 取得位置：Intervals.icu → Settings → Developer Settings。

### 需要的端點

| 用途 | 端點 |
|---|---|
| 活動清單 | `GET /athlete/{id}/activities?oldest={date}&newest={date}` |
| 單筆活動 | `GET /activity/{activityId}` |
| 分段（lap）資料 | `GET /activity/{activityId}/intervals` |
| 健康資料 | `GET /athlete/{id}/wellness?oldest={date}&newest={date}` |

日期格式一律 ISO-8601（`YYYY-MM-DD`）。
`activities` 支援 `fields=` 參數只取需要的欄位，可大幅減少 payload。

### ⚠️ 建置時要先驗證的三件事

這幾點請**先用 curl 打一次確認實際回傳結構再寫解析**，不要照抄假設：

1. **lap 端點的實際路徑與欄位名**。目標是拿到每一趟的 `distance` / `moving_time` /
   `average_heartrate` / `max_heartrate`。欄位名可能帶 `icu_` 前綴。
2. **`wellness` 是否支援日期區間查詢**，還是只能單日 `/wellness/{date}` 逐日拉。
   若只能逐日，加上簡單快取避免重複請求。
3. **HRV 的欄位名稱**（可能是 `hrv` 或 `hrvSDNN`）與單位是否為 ms。

### 需要的欄位

**activities**：`id, start_date_local, name, type, distance, moving_time,
average_heartrate, max_heartrate, icu_training_load, icu_efficiency_factor,
icu_hr_zone_times, description, average_speed`

**wellness**：`id(date), hrv, restingHR, sleepSecs, sleepScore, weight, ctl, atl`

TSB 自行計算：`TSB = CTL − ATL`。

---

## 輸出格式

四個區塊，依序輸出。

### 1. 檔頭

```
=== 訓練摘要 2026-07-30 ~ 2026-08-26 ===
目標　台北馬半程 12/20 · 92 分 (4:22/km) · 剩 116 天
期中　長榮馬 10/25 (測驗賽) · 陽明山 11/1 (體驗跑)
現況　W3 基礎期 · 本週 10.1 / 44 km
配速　E 5:45-6:20 | M 4:40 | T 4:26-4:28 | I 4:05 | R 3:45-3:50  (VDOT 48)
```

### 2. 週統計

每週一行。強度分類依 `plan.json` 的 `sessions` 對照，
對不上的用活動名稱關鍵字推測，再對不上就歸為 E。

```
--- 週統計 ---
W1  08/10-08/16   36.7km  E 24.7 / T 9.5 / I 10.0 / L 0     負荷 182  CTL 30→33
W2  08/17-08/23   39.3km  E 22.3 / T 9.2 / I 0 / L 14.3     負荷 205  CTL 33→36
W3  08/24-08/30   10.1km  進行中（目標 44km）
```

**同時檢查 `rules.quality_caps` 並在超標的那一行後面加註**，例如：
`⚠ I 佔 25%（上限 8%）` 或 `⚠ 長跑佔 36%（上限 30%）`。

### 3. 每日明細（核心區塊）

倒序或正序都可以，但要固定。**沒有訓練的日子只要有 wellness 就也要出現一行**，
因為休息日的恢復數據同樣有判讀價值。

```
08/25 二 [T] 節奏 10.1km 4:28/km 均心-- 最高174 負荷58
      laps  18:20  4.10km  4:28  HR 162→174
            03:22  0.90km  3:45  HR 171
      晨間  HRV 62 (7日 60) · RHR 42 · 睡 7:10
      狀態  CTL 36 ATL 44 TSB -8 · 脫鉤 2.1%
      筆記  中間肚子不舒服中斷去廁所，接續後跑了 3 分鐘
      ⚠ R 段配速 3:45 出現在 T 課之後 — 疲勞下高速，阿基里斯腱風險

08/24 一 [--] 休息
      晨間  HRV 58 (7日 59) · RHR 43 · 睡 6:40
```

**laps 只印「工作段」**，把慢跑恢復段濾掉，判斷規則：
配速快於 E 下限（5:45）且距離大於 150m。
若濾完是空的（例如純 E 跑），整個 laps 區塊就不要印。

`⚠` 那行依 `rules.execution_flags` 產生。這是整份摘要最有價值的部分——
它把「執行是否偏離處方」自動標出來。

### 4. 趨勢摘要

```
--- 趨勢 ---
E 配速@心率140-150   08/12 6:20  →  08/19 6:30  →  08/21 6:09
HRV 7 日平均         57 → 59 → 60  （基線 61-70）
靜止心率             43 → 42 → 42
CTL / TSB            36 / -8
最長單次             14.3km / 88 分（08/21）
```

「E 配速@固定心率」是判斷有氧體能進步最可靠的指標，一定要有。

---

## CLI

```
python digest.py                    # 預設近 4 週
python digest.py --weeks 8
python digest.py --from 2026-08-10 --to 2026-08-26
python digest.py --out digest.txt   # 預設印到 stdout
```

---

## 專案結構

```
running-digest/
├── digest.py          # 進入點 + CLI
├── icu.py             # API client（認證、重試、快取）
├── transform.py       # 原始資料 → 摘要資料結構
├── render.py          # 摘要資料結構 → 文字
├── plan.json          # 週期設定（已提供）
├── .env.example
├── .gitignore
└── README.md
```

分層的理由：階段 2 要接靜態頁時，`render.py` 換成 JSON 輸出即可，
`icu.py` 與 `transform.py` 完全不用動。

---

## 實作順序

先讓每一步跑出東西再往下，不要一次寫完再除錯。

1. `icu.py` — 打通認證，印出最近 5 筆活動的名稱與距離
2. 驗證上面「⚠ 要先確認的三件事」，把實際回傳的 JSON 存成 `sample/` 供對照
3. `transform.py` — 活動層摘要（週統計 + 每日一行）
4. lap 層解析與工作段過濾 ← **最容易出錯的地方，留最多時間**
5. wellness 合併
6. `rules` 檢查與 `⚠` 標記
7. `render.py` 輸出格式化

---

## 不要做的事

- 不要建資料庫。需要快取就寫 JSON 檔到 `.cache/`
- 不要把 API key 寫進任何會 commit 的檔案
- 不要在這個階段做網頁、圖表、或 AI 對話
- 不要為了湊格式而捏造缺漏的欄位；沒有的值印 `--`
- 不要用正規表示式解析 API 回傳，它是結構化 JSON

---

## 完成後

把 `digest.py` 的輸出貼回對話。
第一次跑會看到格式或分類上的問題，我們再一起調 `plan.json` 的對照規則。

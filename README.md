# 訓練日誌

從 Intervals.icu 拉訓練與健康資料，套上 `plan.json` 的週期結構，
產出一份可直接貼進聊天視窗的純文字摘要。

## 安裝

```bash
pip install requests
cp .env.example .env      # 填入自己的 ICU_ATHLETE_ID / ICU_API_KEY
```

API key 在 Intervals.icu → Settings → Developer Settings。
只依賴 `requests`；有裝 `python-dotenv` 會自動使用，沒裝就走內建的簡易 `.env` 解析。

## 使用

```bash
python digest.py                          # 預設近 4 週
python digest.py --weeks 8
python digest.py --from 2026-08-10 --to 2026-08-26
python digest.py --out digest.txt         # 預設印到 stdout
python digest.py --no-cache               # 略過 .cache/
python digest.py --strict-class           # 只用 SPEC 的兩層分類規則

python digest.py --format html --out docs/index.html   # 互動網頁（見下）
python digest.py --format json --out docs/data.json    # 純資料
```

進度訊息走 stderr，摘要本文走 stdout，所以 `python digest.py > digest.txt` 也可以。

## 檔案

| 檔案 | 職責 |
|---|---|
| `digest.py` | CLI、日期區間、串接三層 |
| `icu.py` | API client：Basic Auth、重試、`.cache/` 磁碟快取 |
| `transform.py` | 原始 JSON → 摘要資料結構（分類、分段過濾、規則檢查） |
| `render.py` | 摘要資料結構 → 純文字（貼聊天用） |
| `render_html.py` | 摘要資料結構 → 單一 HTML 檔（資料內嵌） |
| `web/template.html` | 網頁的版面、樣式、互動，全部在這一個檔 |
| `docs/index.html` | 本機產生的網頁（不進 repo） |
| `.github/workflows/update.yml` | 產生加密網頁部署到 GitHub Pages，並把文字摘要推到私人 repo `training-data` |
| `.github/training-data/README.md` | `training-data` 的說明檔（給 Claude 看的閱讀指引） |
| `plan.json` | 週期設定：賽事、配速、rules、weeks、sessions |
| `sample/` | 實際 API 回傳的原始 JSON，寫解析時的對照組（不進 repo） |

`icu.py` 與 `transform.py` 不知道輸出長什麼樣子；文字版和網頁版共用同一份資料結構。

## 網頁版

```bash
python digest.py --weeks 8 --format html --out docs/index.html
```

產生的是**一個自足的 HTML 檔**，資料直接內嵌，沒有外部依賴、不打任何 API，
雙擊就能開。`.env` 有 `DIGEST_PASSWORD` 的話會加密（見下），要本機看明文加 `--plain`。

內容：

- **檔頭**：目標賽倒數、本週跑量進度條、CTL/ATL/TSB、期中賽、目前配速表
- **週統計**：每週一列，強度分布是一條堆疊條；點一列可以篩選下面的每日明細
- **趨勢**：四張圖（體能負荷、HRV、靜止心率、E 配速@固定心率），
  滑鼠或方向鍵移動十字線，每張圖都有「表格」檢視
- **每日明細**：可篩週次、隱藏休息日、只看有提醒的、一鍵展開所有分段
- 深淺色跟系統走，右上角可以手動切；篩選狀態記在瀏覽器裡

課別顏色固定（E 藍、M 橘、T 青、I 黃、R 粉、L 綠），調色盤通過色盲安全驗證。

### 加密

GitHub Pages 免費版只能從公開 repo 發佈，所以網頁內容是加密的：

- 資料用 AES-256-GCM 加密，金鑰由密碼經 PBKDF2-SHA256（60 萬次）衍生
- 打開網頁要輸入密碼（可切換顯示）；勾「記住這台裝置」之後，記的是衍生出來的金鑰而不是密碼，
  每天重新產生網頁仍然有效；**3 天沒打開**就要重新輸入（`web/template.html` 的 `REMEMBER_DAYS`）
- 密碼解不開時會先到伺服器抓最新版再試，避免剛換密碼時瀏覽器拿著快取的舊版
- 換密碼（改 `DIGEST_PASSWORD` secret）之後，所有裝置都會重新要密碼
- 右上角「鎖定」會忘掉這台裝置記住的金鑰

解鎖之後網頁會用 Intervals.icu 的 API key 做兩件事：打開時即時抓今天的晨間資料重算
「今日狀態」，以及把課後筆記寫回活動描述。API key 只放在**加密版**的資料裡
（`render_html.py`），明文版和 `--format json` 都沒有。所以網頁密碼同時保護著
Intervals.icu 帳號的讀寫權限。

**repo 本身是公開的**：程式碼和 `plan.json`（賽事、配速、`athlete.notes`）任何人都看得到。
訓練與健康資料只存在加密的網頁裡，`docs/`、`sample/`、`.env` 都不會進 repo。

密碼的強度就是這一切的強度——用一組沒在別處用過的長密碼。

### 自動更新

`.github/workflows/update.yml` 在三種時候重新產生並部署：

- 每天台灣時間 06:17、09:17、21:17（避開整點；GitHub 排程可能延遲或跳過）
- push 到 `master`（改了程式或 `plan.json`）
- Actions 頁面手動按 **Run workflow**

需要三個 repo secrets：`ICU_ATHLETE_ID`、`ICU_API_KEY`、`DIGEST_PASSWORD`。
最快的設法是在專案資料夾裡跑：

```bash
gh secret set -f .env
```

公開 repo 60 天沒有 commit 時 GitHub 會停用排程；workflow 會每 50 天自己補一個空 commit。

## API 實測結果

SPEC 要求先確認再寫解析。2026-08-27 對 我的帳號 實際打過的結果：

1. **lap 端點**：`GET /activity/{id}/intervals` 回傳
   `{id, analyzed, icu_intervals[], icu_groups[]}`。
   lap 欄位**沒有** `icu_` 前綴，實際是 `distance` / `moving_time` /
   `average_heartrate` / `max_heartrate` / `average_speed` / `type` / `group_id`。
   內容是 Intervals.icu 自動偵測的分段（此帳號多為 1km 自動分割），不是課表結構。
2. **wellness 支援區間查詢**：`?oldest=&newest=` 回傳 list，不需逐日拉。
3. **HRV 欄位名是 `hrv`**（單位 ms）。`hrvSDNN` 同時存在但此帳號恆為 `null`，
   程式會在 `hrv` 缺值時退回讀它。

另外兩個實測發現：

- `GET /athlete/{id}/activities` 回傳的物件**已經是完整活動**（183 個欄位，
  與逐筆 `GET /activity/{id}` 完全相同），所以不需要再逐筆打單筆活動端點。
  程式用 `fields=` 只取需要的欄位，payload 從 125KB 降到約 5KB。
- `decoupling` 與 `icu_efficiency_factor` 在此帳號的跑步活動上全部是 `null`
  （沒有跑步功率計）。摘要照 SPEC 印 `--`，不自行捏造。

## 分類規則

**課表不參與分類。標籤一律反映你實際跑了什麼**，因為課常常會挪到別天、
多練一天、或少練一天。課表跟實際的差距另外用「對照」列出來。

強度（E / M / T / I / R / L）的判定方式：

1. **主課表段依配速分帶**：快於 E 下限、至少 150m 的分段，依配速歸到 R / I / T / M。
   比 M 帶慢的（例如 5:30）只是偏快的輕鬆跑，**不算質量**。
2. **70 分鐘以上**：質量段（R/I/T/M）要**超過全程一半**才算質量課（例如熱身 + 2×5km M），
   否則一律是**長跑**——尾段拉到 M、甚至 T，仍然是長跑。
3. **70 分鐘以下**：質量段至少 1km、且佔全程 15% 以上，才算質量課；
   E 30 分尾巴加 4×150m 加速，主課仍然是 E。
4. 是質量課的話，取距離最多的配速帶（一樣多取快的）。不取最快的帶——
   一趟收尾加速不該把整堂節奏跑升級成間歇課。
5. 資料看不出東西時，才看活動名稱關鍵字（節奏 / 間歇 / 長跑 …），都沒有就是 E。

配速分帶的界線從 `plan.json` 的 `paces` 算：取「這一帶的中心」和「下一個較慢帶的最快端」
的中點。以 VDOT 48 的配速為例：R ≤ 3:50、I ≤ 4:10、T ≤ 4:28、M ≤ 5:11，其餘 E。
長榮馬後更新 `paces`，分類會自動跟著變。

網頁的 `classifyRun()` 是同一套規則：今天剛跑完、網頁還沒重新產生時，
打開網頁會當場抓分段分類（標「即時」），不用等排程。

`--strict-class` 會退回 SPEC 原本的規則（課表優先 → 名稱關鍵字 → E）。
用你的資料跑會發現整份摘要幾乎全是 E，這就是不採用它的原因。

## 課表對照

`plan.json` 的 `sessions` 只當對照用，不當標籤。有排課的日期範圍內：

- **每日明細**：跟課表不一樣的那天才會多出一行「對照」，照著做就不印
  - `對照  處方 E 4km ／ 實際 I 10.8km — 強度高於處方，多 6.8km`
  - `對照  處方 I 10.5km — 未執行`
  - `對照  課表未排 ／ 實際 E 5.0km — 額外訓練`
- **週統計**：多一行「課表」總結
  - `課表  處方 4 課 36.5km ／ 實際 3 課 32.1km · 未執行 08/28 I`

距離差 20% 以內、強度相同視為照做（`transform.KM_TOLERANCE`）。
還沒到的日子不算未執行。

## 主課表段過濾

`laps` 區塊照 SPEC：配速快於 E 下限（5:45）且距離大於 150m。
濾完是空的就整個區塊不印。

`⚠` 規則檢查另外再收一次：只看「至少和本課強度一樣快」的分段。
不然 5:43 的熱身公里會被當成節奏趟，拿去比趟末心率會得出 33 bpm 這種假警訊。

## plan.json 規則實作狀況

| 規則 | 狀態 |
|---|---|
| `execution_flags` | ✅ 5 條全部實作，門檻數字在 `transform.py` 常數，措辭讀 `plan.json` |
| `quality_caps` | ✅ T / I / R / M 百分比上限、M 總分鐘上限 |
| `long_run` | ✅ 佔週跑量百分比、單次分鐘上限 |
| `interval_design` | ✅ 只檢查 I 課的趟長；60 秒以內的算加速跑，不當間歇趟 |
| `volume_ramp` | ✅ 週增幅上限 + 連續增量提醒，只比對完整的相鄰週 |
| `hrv_decision` | ✅ 5 條規則，第 5 條待命中（見下） |

`athlete` 區塊用到 `hrv_baseline`（HRV 判讀）與 `hr_rest`（靜止心率比對）；
`lthr` / `hr_max` / `notes`（阿基里斯腱舊傷）尚未進入判斷邏輯。

### hrv_decision 的判讀方式

HRV 低於 `hrv_baseline` 下緣的日子才會出聲，由重到輕只報最嚴重的一條：

1. 有身體不適紀錄 → 停練
2. 靜止心率同時較 7 日均高 5 bpm 以上 → 休息
3. 7 日平均連續 5 天以上低於基線下緣 → 減量
4. 單日值連續 2 天以上偏低 → 質量課降級
5. 單日偏低但前一天有質量課 → 正常，照計畫走（不加 ⚠，只寫「註記」）

第 3 條會一路持續好幾天，所以**只在剛跨過門檻那天報一次**，之後的狀況看趨勢
區塊的「現況判讀」。

第 1 條需要 intervals.icu 的主觀欄位（`injury` / `soreness` / `fatigue` /
`comments`）。這個帳號目前四個都沒填，所以規則等於待命 —— 要它生效就得在
intervals.icu 的每日記錄裡填。`soreness` / `fatigue` 是 1-4 分，
程式當 ≥ 3 才算不適（`transform.SUBJECTIVE_BAD`）。

## 已知的調整點

- `transform.E_HR_WINDOW`：趨勢區塊「E 配速@固定心率」的心率窗，預設 140-150。
  點數不足（少於 3 點）時程式會自動放寬 ±5 / ±10，短於 3km 的熱身緩和片段不算，
  實際使用的窗會印在該行標題上。
- `transform.LONG_RUN_MIN_MINUTES`：多久算長跑，預設 70 分。
- `transform.QUALITY_MIN_KM` / `QUALITY_MIN_SHARE`：70 分鐘以下的跑步，質量段要多少才算質量課，
  預設「至少 1km 且佔全程 15%」。
- `transform.LONG_QUALITY_SHARE`：70 分鐘以上的跑步，質量段要佔多少才不算長跑，預設 50%。
- `transform.REP_MIN_SECONDS`：短於幾秒算加速跑而非間歇趟，預設 60 秒。
- `transform.HRV_RHR_RISE_BPM` / `HRV_STREAK_DAYS` / `HRV_STREAK7_DAYS`：
  hrv_decision 的三個門檻，預設 5 bpm / 2 天 / 5 天。
- `plan.json` 的 `athlete.hrv_baseline`：基線設得太高的話，「7 日平均連續低於基線」
  會一直亮。累積一個月資料後值得用實測值重新校準。
- `transform.RUN_TYPES`：哪些活動類型計入跑量。網球、重訓、飛輪不計跑量，
  但訓練負荷仍計入週負荷（因為 CTL/ATL 本來就含它們）。
- `quality_caps`：照丹尼爾原意，檢查的是**單堂課**在 M/T/I/R 配速實際跑了多少
  （主課表段依配速歸類），對比週跑量的百分比；進行中的週用目標跑量當分母；
  測驗日不算；超過上限 `transform.CAP_TOLERANCE`（10%）以上才標。

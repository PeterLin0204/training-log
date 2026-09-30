# 專案進度

更新：2026-09-30

**一句話：上線了。網址 https://peterlin0204.github.io/training-log/ ，每天台北 06:30 自動更新。**

---

## 上線進度

| 項目 | 狀態 |
|---|---|
| repo | https://github.com/PeterLin0204/training-log （公開，只有程式碼與 plan.json） |
| 網頁 | https://peterlin0204.github.io/training-log/ （加密，要密碼） |
| 自動更新 | 每天台北 06:30；push 程式或 plan.json 也會立即重新發佈 |
| Secrets | `ICU_ATHLETE_ID`、`ICU_API_KEY`、`DIGEST_PASSWORD` |
| 已驗證 | 線上 HTML 只有加密資料，搜尋地名、賽事名、帳號、API key 都是 0 筆 |

### 之後要改東西

- **改課表**：編輯 `plan.json` → `git commit` → `git push`，幾分鐘內網頁更新
- **換密碼**：改 `.env` 的 `DIGEST_PASSWORD` → `gh secret set -f .env` → Actions 頁按 Run workflow
- **手動更新**：https://github.com/PeterLin0204/training-log/actions → update digest → Run workflow

### 加密做了哪些測試

- 錯密碼 → 擋下，顯示「密碼不對」
- 對密碼 → 解開，56 天、7 週、4 張圖完整顯示
- 重新整理 → 自動解鎖（記住的是金鑰不是密碼）
- 隔天重新產生網頁、同一組密碼 → 仍自動解鎖
- 換密碼 → 舊裝置自動要求重新輸入
- 加密後的 HTML 搜尋地名、賽事名、欄位值 → 一個都找不到

---

## 階段 1：文字摘要 ✅

`python digest.py` 印出純文字摘要，貼進對話用。

- 四個區塊：檔頭 / 週統計 / 每日明細 / 趨勢
- `plan.json` 的 6 條規則全部實作
- 課表 vs 實際對照（挪課、漏練、多練）
- 分類以實際跑的為準，課表只當對照

## 階段 2：互動網頁 ✅

`python digest.py --format html --out docs/index.html`

- 單一 HTML、資料內嵌、雙擊就能開
- 四張圖（體能負荷、HRV、靜止心率、E 配速），十字線讀數、表格檢視
- 點週次篩選、隱藏休息日、只看有提醒的、展開分段
- 深淺色、手機版面
- 有 `DIGEST_PASSWORD` 就加密，`--plain` 產明文給本機看

---

## 09/27 更新資料時順手修的

| 問題 | 修法 |
|---|---|
| W5 顯示「週量 +58%」 | W4 是計畫中的減量週，回升不算暴增；改跟減量前的 W3 比（+1%） |
| 09/08 測驗被當成 T 課，跳「趟末心率差 20 bpm」 | `plan.json` 的 TEST 從 09/05 搬到 09/08（你說測驗改到那天） |
| E 配速趨勢混進 1.4km 的緩和片段 | 短於 3km 的 E 跑不列入趨勢 |
| 網頁重新整理後有時卡住 | 解密太快、頁面還沒讀完；改成等頁面讀完再啟動 |

## 待你處理的（plan.json）

- [ ] `sessions` 只排到 09/08，W6 之後沒有課表可以對照
- [ ] `meta.updated` 還停在 2026-08-26
- [ ] `athlete.hrv_baseline` 累積夠資料後用實測值重新校準

## 資料面的限制（不是程式的問題）

- `decoupling` 全空（沒有跑步功率計）→「脫鉤」永遠 `--`
- 沒寫課後筆記 →「筆記」不會出現
- 主觀欄位（傷病 / 痠痛 / 疲勞）沒填 → HRV 第 5 條規則待命中
- `quality_caps` 拿整堂課距離算百分比，所以每週都亮 T/I 超標（已接受）

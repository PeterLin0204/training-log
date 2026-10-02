"""原始 API 資料 → 摘要資料結構。

這一層不碰任何輸出格式；render.py 才負責排版。
階段 2 要接靜態頁時，把這裡的 dataclass 直接 asdict() 丟成 JSON 即可。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------- 可調常數 ----------

# 計入「跑量」的活動類型。網球 / 重訓 / 飛輪不算跑量，但訓練負荷仍計入。
RUN_TYPES = {"Run", "VirtualRun", "TrailRun", "Treadmill", "TrackRun"}

# 主課表段判定（SPEC）：配速快於 E 下限，且距離大於 150m
WORK_LAP_MIN_METERS = 150

# 沒有課表對照時，多久以上的跑步視為長跑
LONG_RUN_MIN_MINUTES = 70

# 主課表段要佔多少，才算「質量課」而不是「輕鬆跑後面加幾趟加速」。
# 長跑的門檻更高：95 分鐘長跑最後 2km 拉到 M/T，仍然是長跑，不是節奏課。
QUALITY_MIN_KM = 1.0
QUALITY_MIN_SHARE = 0.15
QUALITY_MIN_SHARE_LONG = 0.30

# 短於這個秒數的算加速跑，不當成間歇趟（檢查趟長時要排除）
REP_MIN_SECONDS = 60

# hrv_decision 的門檻（plan.json 的規則是文字敘述，數字在這裡）
HRV_RHR_RISE_BPM = 5        # 「靜止心率升高 5 bpm 以上」
HRV_STREAK_DAYS = 2         # 「連續 2-3 天明顯低於基線」
HRV_STREAK7_DAYS = 5        # 「7 日平均連續 5-7 天低於基線下緣」

# 算「前一天有質量課」時，哪些課別算質量課
QUALITY_CLASSES = {"T", "I", "R", "M", "TEST"}

# intervals.icu 的 soreness / fatigue 是 1-4 分，數字越大越差
SUBJECTIVE_BAD = 3

# quality_caps 超過上限多少才標。分段是 1 公里自動切的，頭尾會混進慢的部分，
# 質量段距離本身就有 ±0.5km 左右的誤差；4.0km 對 3.96km 這種擦邊不值得一個警示。
CAP_TOLERANCE = 0.10

# 每日記錄的備註提到這些字，才當作 hrv_decision 第 5 條的「生病」
ILLNESS_WORDS = ("喉嚨", "感冒", "發燒", "體溫", "生病", "咳", "鼻水", "鼻塞", "頭痛", "腸胃", "拉肚子")

# 今日狀態：靜止心率比前 7 天平均高幾下算黃燈 / 紅燈（紅燈門檻就是 hrv_decision 的 5 bpm）
RHR_YELLOW_BPM = 3
# intervals.icu 的主觀欄位，全部是 1-4 分、數字越大越差
#（疲勞 / 痠痛 / 壓力：1 低 → 4 極高；心情：1 很好 → 4 很差；動機：1 極高 → 4 很低；傷病：1 無 → 4 受傷）
SUBJECTIVE_FIELDS = [("fatigue", "疲勞"), ("soreness", "痠痛"), ("stress", "壓力"),
                     ("mood", "心情"), ("motivation", "動機"), ("injury", "傷病")]

# 趨勢區塊「E 配速@固定心率」的心率窗。點太少會自動放寬，實際使用的窗會印在標題上。
E_HR_WINDOW = (140, 150)
E_TREND_MIN_KM = 3.0          # 短於這個距離的 E 跑多半是熱身 / 緩和的片段，不列入趨勢
E_TREND_MIN_POINTS = 3

# 週期表裡這些 phase 是計畫中的減量；它的下一週回升不算「週量暴增」
DELOAD_PHASES = ("減量", "恢復")

# execution_flags 的門檻。優先讀 plan.json：paces 區塊有值就用 paces，
# 沒有就從 execution_flags 的文字抓數字（「快於處方 5 秒」→ 5）。這兩個是最後備援。
T_PACE_TOLERANCE_SECONDS = 5   # T 課配速快於處方 5 秒以上
T_HR_SPREAD_BPM = 5            # T 課兩趟趟末心率差異大於 5 bpm

WEEKDAY_ZH = "一二三四五六日"


# ---------- 小工具 ----------

def parse_pace(text):
    """'4:28' → 268.0 秒/公里"""
    if not text:
        return None
    parts = str(text).split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return None


def fmt_pace(seconds_per_km) -> str:
    if not seconds_per_km or seconds_per_km <= 0:
        return "--"
    total = int(round(seconds_per_km))
    return f"{total // 60}:{total % 60:02d}"


def fmt_clock(seconds) -> str:
    if not seconds:
        return "--"
    total = int(round(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def fmt_hm(seconds) -> str:
    if not seconds:
        return "--"
    total = int(round(seconds))
    return f"{total // 3600}:{(total % 3600) // 60:02d}"


def to_date(value) -> dt.date:
    return dt.date.fromisoformat(str(value)[:10])


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def load_plan(path="plan.json") -> dict:
    text = Path(path).read_text(encoding="utf-8-sig")
    duplicates = []

    def keep_last(pairs):
        # JSON 允許同一個鍵出現兩次，後面的會悄悄蓋掉前面的——
        # 同一天排了兩次課，前一筆就消失了，所以要講出來
        seen = set()
        for key, _ in pairs:
            if key in seen:
                duplicates.append(key)
            seen.add(key)
        return dict(pairs)

    try:
        plan = json.loads(text, object_pairs_hook=keep_last)
        for key in duplicates:
            print(f"[warn] {path} 裡「{key}」出現不只一次，只會用最後一筆", file=sys.stderr)
        return plan
    except json.JSONDecodeError as exc:
        # plan.json 是手動編輯的，最常見的錯是少逗號或多逗號，直接指出第幾行
        line = text.splitlines()[exc.lineno - 1] if exc.lineno <= len(text.splitlines()) else ""
        raise SystemExit(
            f"錯誤：{path} 第 {exc.lineno} 行格式不對（{exc.msg}）\n"
            f"    {line.strip()}\n"
            f"常見原因：上一行結尾少了逗號、最後一筆後面多了逗號、引號沒成對。"
        ) from None


# ---------- 配速分帶 ----------

def _band_center(spec):
    if not spec:
        return None
    if spec.get("target"):
        return parse_pace(spec["target"])
    lo, hi = parse_pace(spec.get("min")), parse_pace(spec.get("max"))
    if lo and hi:
        return (lo + hi) / 2
    return lo or hi


def build_pace_bands(paces: dict):
    """回傳 [(強度, 上界秒數)]，由快到慢；上界取相鄰兩帶中心點的中位。

    例：R 227.5 / I 245 / T 267 / M 280 / E 362.5
        → R<=236, I<=256, T<=273.5, M<=321, 其餘 E
    """
    order = ["R", "I", "T", "M", "E"]
    centers = [(k, _band_center(paces.get(k))) for k in order]
    centers = [(k, c) for k, c in centers if c]
    bands = []
    for (k1, c1), (_k2, c2) in zip(centers, centers[1:]):
        bands.append((k1, (c1 + c2) / 2))
    if centers:
        bands.append((centers[-1][0], float("inf")))
    return bands


def prescribed_pace(spec, edge="min"):
    """處方配速的秒數。paces 可能寫成 {target} 也可能寫成 {min, max}，兩種都吃。"""
    if not spec:
        return None
    return parse_pace(spec.get(edge)) or parse_pace(spec.get("target"))


def band_for_pace(pace_s, bands):
    if not pace_s:
        return None
    for name, upper in bands:
        if pace_s <= upper:
            return name
    return bands[-1][0] if bands else None


# ---------- 資料結構 ----------

@dataclass
class Lap:
    seconds: float
    meters: float
    pace_s: float | None
    hr_avg: int | None
    hr_max: int | None


@dataclass
class Activity:
    id: str
    date: str
    name: str
    sport: str
    is_run: bool
    km: float
    seconds: float
    pace_s: float | None
    hr_avg: int | None
    hr_max: int | None
    load: int | None
    decoupling: float | None
    efficiency_factor: float | None
    description: str | None
    cls: str                 # E / M / T / I / R / L / XT
    cls_source: str          # plan / name / pace / duration / default
    plan_desc: str | None
    work_laps: list = field(default_factory=list)
    flags: list = field(default_factory=list)


@dataclass
class Wellness:
    date: str
    hrv: float | None
    resting_hr: int | None
    sleep_secs: int | None
    sleep_score: float | None
    weight: float | None
    ctl: float | None
    atl: float | None
    tsb: float | None
    hrv7: float | None
    rhr7: float | None
    illness: str | None = None        # 主觀不適紀錄，hrv_decision 第 5 條要用
    subjective: dict = field(default_factory=dict)   # {"疲勞": 2, ...}，只放有填的


@dataclass
class Day:
    date: str
    weekday: str
    activities: list
    wellness: Wellness | None
    plan_type: str | None = None      # 課表排的強度
    plan_km: float | None = None      # 課表排的距離
    plan_desc: str | None = None      # 課表的課程敘述
    deviation: str | None = None      # 與課表的差異；照著做就是 None
    hrv_note: str | None = None       # hrv_decision 的判讀
    hrv_level: str | None = None      # ok / warn / alert / info
    hrv_key: str | None = None        # 觸發的是哪一條規則
    hrv_repeat: bool = False          # 與前一天同一條長期規則，輸出時省略


@dataclass
class Week:
    n: int | None
    start: str
    end: str
    phase: str
    target_km: float | None
    km: float
    by_class: dict
    load: int
    ctl_start: float | None
    ctl_end: float | None
    in_progress: bool
    warnings: list
    note: str | None
    plan_count: int = 0               # 課表排了幾課（不含 OFF）
    plan_km: float = 0.0
    done_count: int = 0               # 實際做了幾課
    done_km: float = 0.0
    missed: list = field(default_factory=list)   # 排了沒做
    extra: list = field(default_factory=list)    # 課表沒排但有跑
    quality_km: dict = field(default_factory=dict)   # 主課表段實際落在 M/T/I/R 配速的距離


@dataclass
class Trends:
    e_hr_window: tuple
    e_pace_points: list
    hrv7_points: list
    hrv_baseline: list | None
    rhr7_points: list
    ctl: float | None
    atl: float | None
    tsb: float | None
    longest: tuple | None
    hrv_status: str | None = None     # 最近一天的 HRV 判讀
    e_min_km: float = E_TREND_MIN_KM  # 網頁的 E 配速圖用同一個門檻


@dataclass
class Signal:
    name: str                 # 靜止心率 / HRV / 主觀
    value: str                # 顯示用的值
    ref: str                  # 比較對象
    light: str                # green / yellow / red / gray
    note: str = ""


@dataclass
class Today:
    date: str                 # 今天
    weekday: str
    data_date: str | None     # 實際用哪一天的晨間資料（今天還沒同步就用前一天）
    light: str                # green / yellow / red / gray
    headline: str             # 可以照課表練 / 質量課降級 / 休息
    prescription: str         # 今天具體要做什麼
    reasons: list             # 為什麼是這個燈
    signals: list
    session: dict | None      # plan.json 今天排的課
    week_note: str | None = None      # 7 日平均連續偏低這種「本週」層級的提醒


@dataclass
class Digest:
    date_from: str
    date_to: str
    goal_race: dict | None
    goal_days_left: int | None
    interim_races: list
    current_week: Week | None
    paces: dict
    vdot: dict
    weeks: list
    days: list
    trends: Trends
    today: Today | None = None
    today_rules: dict | None = None   # 網頁打開時即時重算「今日狀態」用，數字跟 build_today 同一份
    schedule: dict | None = None      # 未來課表：網頁的「明日課表」「接下來的課表」用


EMPTY_WELLNESS = Wellness("", None, None, None, None, None, None, None, None, None, None, None)


# ---------- 分段解析 ----------

def parse_laps(raw_intervals, e_pace_limit):
    """把 icu_intervals 轉成主課表段清單。

    已驗證：欄位名無 icu_ 前綴，是 distance / moving_time /
    average_heartrate / max_heartrate / average_speed。

    SPEC 的過濾規則：配速快於 E 下限（5:45）且距離大於 150m。
    """
    laps = []
    for iv in raw_intervals or []:
        meters = iv.get("distance")
        seconds = iv.get("moving_time") or iv.get("elapsed_time")
        if not meters or not seconds or meters < WORK_LAP_MIN_METERS:
            continue
        pace_s = seconds / (meters / 1000.0)
        if e_pace_limit and pace_s >= e_pace_limit:
            continue
        laps.append(Lap(
            seconds=float(seconds),
            meters=float(meters),
            pace_s=pace_s,
            hr_avg=iv.get("average_heartrate"),
            hr_max=iv.get("max_heartrate"),
        ))
    return laps


# ---------- 強度分類 ----------

NAME_KEYWORDS = [
    ("I", ("間歇", "interval", "vo2", "x1000", "x800", "x400")),
    ("T", ("節奏", "閾值", "tempo", "threshold")),
    ("R", ("加速", "反覆", "strides", "repetition")),
    ("M", ("馬配", "馬拉松配速", "marathon pace")),
    ("L", ("長跑", "long run", "lsd")),
    ("E", ("輕鬆", "恢復", "easy", "recovery")),
]


def classify(act: dict, plan: dict):
    """SPEC 的兩層規則：plan.json sessions 對照 → 活動名稱關鍵字。

    只在 --strict-class 下使用。預設走 infer_class，因為課表跟實際常常不一樣
    （課挪到別天、多練一天、少練一天），標籤要反映實際跑了什麼，
    課表留給 compare_day 做對照。
    """
    date = str(act.get("start_date_local", ""))[:10]
    session = (plan.get("sessions") or {}).get(date)
    if session and session.get("type"):
        return session["type"], "plan", session.get("desc")

    name = (act.get("name") or "").lower()
    for cls, keywords in NAME_KEYWORDS:
        if any(k in name for k in keywords):
            return cls, "name", None
    return "", "", None


def classify_by_name(act: dict):
    """活動名稱關鍵字。Garmin 預設名稱是地名，通常對不上；手動改過名的才有用。"""
    name = (act.get("name") or "").lower()
    for cls, keywords in NAME_KEYWORDS:
        if any(k in name for k in keywords):
            return cls, "name"
    return "", ""


def infer_class(act: dict, laps, bands):
    """預設的分類方式：用實際跑出來的東西回推強度，不看課表。

    規則：取「累積工作距離最多的分帶」當課別。不是取最快的分帶——一趟收尾加速
    不該把整堂節奏跑升級成間歇課。時長夠長而主帶是 E/M 的，歸為長跑。

    為什麼不信課表：課表是計畫，實際常常不一樣——課挪到別天、多練一天、
    少練一天。標籤要回答「我做了什麼」，課表跟實際的差距交給 compare_day
    另外列出來。要退回 SPEC 原本的「課表優先」規則，用 --strict-class。
    """
    seconds = act.get("moving_time") or 0
    is_long = seconds >= LONG_RUN_MIN_MINUTES * 60

    total_m = act.get("distance") or 0
    work_m = sum(l.meters for l in laps)
    # 主課表段太少就不算質量課：E 30 分尾巴加 4x150m，主課仍然是 E
    share = QUALITY_MIN_SHARE_LONG if is_long else QUALITY_MIN_SHARE
    is_quality = work_m >= max(QUALITY_MIN_KM * 1000, total_m * share)

    if laps and is_quality:
        by_band = {}
        for lap in laps:
            band = band_for_pace(lap.pace_s, bands)
            if band:
                by_band[band] = by_band.get(band, 0.0) + lap.meters
        if by_band:
            dominant = max(by_band, key=lambda b: by_band[b])
            if is_long and dominant in ("E", "M"):
                return "L", "duration"
            return dominant, "pace"

    if is_long:
        return "L", "duration"
    return "E", "default"


# ---------- execution_flags ----------

_FLAG_NEEDLES = [
    ("T_too_fast", "T 課配速快於處方"),
    ("T_uneven", "T 課兩趟趟末心率"),
    ("I_weak", "I 課趟末心率未達"),
    ("R_too_fast", "R 課配速快於"),
    ("E_too_hard", "E 課平均心率高於"),
]


def _advice_map(plan: dict):
    """把 plan.json 的 execution_flags 文字拆成 {檢查 id: 建議文字}。

    改 plan.json 的措辭會反映到輸出；數字門檻留在程式碼常數裡。
    """
    out = {}
    for line in (plan.get("rules", {}).get("execution_flags") or []):
        for flag_id, needle in _FLAG_NEEDLES:
            if needle in line:
                tail = line.split("→", 1)[1].strip() if "→" in line else line
                tail = tail.removeprefix("標記，").removeprefix("標記").strip()
                out[flag_id] = tail
    return out


def quality_laps(laps, cls, bands):
    """只留「至少和本課強度一樣快」的分段。

    SPEC 的主課表段規則（快於 5:45 且大於 150m）會把 5:43 的熱身公里也算進來，
    拿它跟節奏趟比趟末心率會得出 33 bpm 這種假警訊。laps 區塊照 SPEC 原樣印，
    但規則檢查只看真正的質量趟。
    """
    ceiling = dict(bands).get(cls)
    if ceiling is None:
        return laps
    return [l for l in laps if l.pace_s and l.pace_s <= ceiling]


def _flag_thresholds(plan):
    """從 execution_flags 的文字抓數字：「快於處方 5 秒」→ 5、「快於 3:45」→ 225。

    paces 區塊有對應值時以 paces 為準（那是測驗後會更新的區塊），這裡只是備援，
    讓 paces 把 I.hr 拿掉之後「趟末心率未達 185」這條規則不會無聲消失。
    """
    out = {}
    for line in (plan.get("rules", {}).get("execution_flags") or []):
        for flag_id, needle in _FLAG_NEEDLES:
            if needle not in line:
                continue
            pace = re.search(r"(\d+):(\d{2})", line)
            if pace:
                out[flag_id] = int(pace.group(1)) * 60 + int(pace.group(2))
            else:
                number = re.search(r"\d+", line)
                if number:
                    out[flag_id] = int(number.group())
    return out


def check_flags(act: Activity, plan: dict, bands):
    flags = []
    laps = quality_laps(act.work_laps, act.cls, bands)
    paces = plan.get("paces") or {}
    rules = plan.get("rules") or {}
    advice = _advice_map(plan)
    thresholds = _flag_thresholds(plan)

    def add(flag_id, observation):
        tail = advice.get(flag_id)
        flags.append(f"{observation} — {tail}" if tail else observation)

    # 門檻來源順序：paces → execution_flags 文字 → 程式常數
    t_min = prescribed_pace(paces.get("T"))
    r_min = prescribed_pace(paces.get("R")) or thresholds.get("R_too_fast")
    i_hr = (paces.get("I") or {}).get("hr") or []
    i_hr_target = i_hr[0] if i_hr else thresholds.get("I_weak")
    e_hr = (paces.get("E") or {}).get("hr") or []
    e_hr_cap = e_hr[1] if len(e_hr) >= 2 else thresholds.get("E_too_hard")
    t_tolerance = thresholds.get("T_too_fast", T_PACE_TOLERANCE_SECONDS)
    t_spread = thresholds.get("T_uneven", T_HR_SPREAD_BPM)

    # 課表排的是測驗 / 比賽的話，本來就是全力跑，T 課的配速規則不適用
    planned = ((plan.get("sessions") or {}).get(act.date) or {}).get("type")
    is_race_day = planned in ("TEST", "RACE")

    if act.cls == "T" and laps and not is_race_day:
        paced = [l.pace_s for l in laps if l.pace_s]
        fastest = min(paced) if paced else None
        if t_min and fastest and fastest < t_min - t_tolerance:
            add("T_too_fast",
                f"T 段配速 {fmt_pace(fastest)} 快於處方 {fmt_pace(t_min)} 達 {t_min - fastest:.0f} 秒")

        end_hrs = [l.hr_max for l in laps if l.hr_max]
        if len(end_hrs) >= 2 and max(end_hrs) - min(end_hrs) > t_spread:
            add("T_uneven",
                f"T 段趟末心率差 {max(end_hrs) - min(end_hrs)} bpm（{max(end_hrs)} / {min(end_hrs)}）")

    if act.cls == "I" and laps and i_hr_target:
        end_hrs = [l.hr_max for l in laps if l.hr_max]
        if end_hrs and max(end_hrs) < i_hr_target:
            add("I_weak", f"I 段趟末心率最高 {max(end_hrs)} 未達 {i_hr_target}")

    if act.work_laps and r_min:
        too_fast = [l.pace_s for l in act.work_laps if l.pace_s and l.pace_s < r_min]
        if too_fast:
            tail = advice.get("R_too_fast", "")
            # 加速跑排在 E / L 課尾巴是正常課表，不是疲勞下高速
            where = ("— " if act.cls in ("R", "E", "L")
                     else f"出現在 {act.cls} 課之後 — 疲勞下高速，")
            flags.append(f"R 段配速 {fmt_pace(min(too_fast))} 快於 {fmt_pace(r_min)} "
                         f"{where}{tail}".strip())

    if act.cls == "E" and act.hr_avg and e_hr_cap and act.hr_avg > e_hr_cap:
        add("E_too_hard", f"E 課平均心率 {act.hr_avg} 高於 {e_hr_cap}")

    flags += check_interval_design(act, bands, rules)
    return flags


def check_interval_design(act: Activity, bands, rules):
    """rules.interval_design：間歇單趟長度。

    只看落在 I 分帶的趟，所以尾巴的 150m 加速跑不會被誤判成「趟長不足」。
    """
    design = rules.get("interval_design") or {}
    lo_s, hi_s = design.get("I_rep_min_seconds"), design.get("I_rep_max_seconds")
    if act.cls != "I" or not (lo_s or hi_s):
        return []

    # 用「至少和 I 一樣快」而不是「剛好落在 I 帶」：3:55 這種只差 1 秒就掉進
    # R 帶的趟，仍然是間歇趟。真正的加速跑用秒數排除，不用配速。
    reps = [l for l in quality_laps(act.work_laps, "I", bands)
            if l.seconds >= REP_MIN_SECONDS]
    if not reps:
        return []

    note = (design.get("note") or "").strip()
    durations = [l.seconds for l in reps]

    def flag(bad, word, limit):
        obs = (f"I 段 {len(bad)}/{len(reps)} 趟單趟 {min(bad):.0f}-{max(bad):.0f} 秒，"
               f"{word} {limit:g} 秒")
        return [f"{obs} — {note}" if note else obs]

    if lo_s and min(durations) < lo_s:
        return flag([d for d in durations if d < lo_s], "短於下限", lo_s)
    if hi_s and max(durations) > hi_s:
        return flag([d for d in durations if d > hi_s], "長於上限", hi_s)
    return []


# ---------- 組裝 ----------

def _hrv_of(row):
    """HRV 欄位名已驗證為 hrv（ms）；hrvSDNN 同時存在但此帳號恆為 null，列為備援。"""
    value = row.get("hrv")
    return value if value is not None else row.get("hrvSDNN")


def _illness_hint(row):
    """主觀不適紀錄，給 hrv_decision 的第 5 條規則（喉嚨痛 / 體溫異常）用。

    intervals.icu 的 injury / soreness / fatigue 是 1-4 分，數字越大越差；
    comments 是自由文字，只有提到生病相關的字才算——寫「睡不好」不該讓今天亮紅燈。
    """
    bits = []
    for field_name, label in (("injury", "傷病"), ("soreness", "痠痛"), ("fatigue", "疲勞")):
        value = row.get(field_name)
        if isinstance(value, (int, float)) and value >= SUBJECTIVE_BAD:
            bits.append(f"{label} {value:g}")
    note = (row.get("comments") or "").strip()
    if note and any(word in note for word in ILLNESS_WORDS):
        bits.append(note[:30])
    return " · ".join(bits) or None


def build_wellness(rows):
    by_date = {r["id"]: r for r in rows if r.get("id")}
    dates = sorted(by_date)
    out = {}
    for i, date in enumerate(dates):
        row = by_date[date]
        window = [by_date[d] for d in dates[max(0, i - 6): i + 1]]
        ctl, atl = row.get("ctl"), row.get("atl")
        out[date] = Wellness(
            date=date,
            hrv=_hrv_of(row),
            resting_hr=row.get("restingHR"),
            sleep_secs=row.get("sleepSecs"),
            sleep_score=row.get("sleepScore"),
            weight=row.get("weight"),
            ctl=ctl,
            atl=atl,
            tsb=(ctl - atl) if ctl is not None and atl is not None else None,
            hrv7=mean([_hrv_of(w) for w in window]),
            rhr7=mean([w.get("restingHR") for w in window]),
            illness=_illness_hint(row),
            subjective={label: row[k] for k, label in SUBJECTIVE_FIELDS
                        if isinstance(row.get(k), (int, float))},
        )
    return out


def build_activities(raw, intervals, plan, bands, e_pace_limit, strict_class):
    out = []
    for a in raw:
        sport = a.get("type") or ""
        is_run = sport in RUN_TYPES
        km = (a.get("distance") or 0) / 1000.0
        seconds = a.get("moving_time") or 0
        laps = parse_laps(intervals.get(a["id"], []), e_pace_limit) if is_run else []

        date = str(a.get("start_date_local", ""))[:10]
        session = (plan.get("sessions") or {}).get(date)
        desc = session.get("desc") if session else None

        if not is_run:
            cls, source = "XT", "sport"
        elif strict_class:
            cls, source, _ = classify(a, plan)            # SPEC 原本的課表優先規則
            if not cls:
                cls, source = "E", "default"
        else:
            cls, source = infer_class(a, laps, bands)     # 預設：以實際跑的為準
            if source == "default":                       # 資料看不出東西，才問活動名稱
                named, named_source = classify_by_name(a)
                if named:
                    cls, source = named, named_source

        act = Activity(
            id=a["id"],
            date=date,
            name=a.get("name") or "",
            sport=sport,
            is_run=is_run,
            km=km,
            seconds=seconds,
            pace_s=(seconds / km) if is_run and km > 0 and seconds else None,
            hr_avg=a.get("average_heartrate"),
            hr_max=a.get("max_heartrate"),
            load=a.get("icu_training_load"),
            decoupling=a.get("decoupling"),
            efficiency_factor=a.get("icu_efficiency_factor"),
            description=(a.get("description") or "").strip() or None,
            cls=cls,
            cls_source=source,
            plan_desc=desc,
            work_laps=laps,
        )
        if is_run:
            act.flags = check_flags(act, plan, bands)
        out.append(act)
    out.sort(key=lambda x: x.date, reverse=True)
    return out


CLASS_ORDER = ["E", "M", "T", "I", "R", "L"]


def quality_volume(runs, bands):
    """每一段主課表段依「實際配速」歸到 M/T/I/R，回傳 (公里, 秒)。

    丹尼爾的 T 10% / I 8% / R 5% 上限算的是「在那個強度跑了多少」，
    不是「那堂課總共幾公里」——10km 的節奏課，熱身緩和 5km 不算 T。
    反過來，長跑最後 3km 拉到 M 配速，那 3km 也要算進 M。
    """
    km = {c: 0.0 for c in ("M", "T", "I", "R")}
    sec = {c: 0.0 for c in ("M", "T", "I", "R")}
    for a in runs:
        for lap in a.work_laps:
            band = band_for_pace(lap.pace_s, bands)
            if band in km:
                km[band] += lap.meters / 1000
                sec[band] += lap.seconds
    return km, sec


def session_cap_warnings(runs, plan, caps, bands, week_km):
    """rules.quality_caps：丹尼爾的上限是「單堂課」的量，不是一週加總。

    原文是「任何一次訓練中，T 配速的量不超過週跑量的 10%」（I 8%、R 5%、M 20%），
    M 另有單次 110 分鐘上限。只算主課表段實際落在該配速帶的距離，
    課表排 TEST / RACE 的日子不算（那本來就是全力跑）。
    """
    if week_km <= 0:
        return []
    sessions = plan.get("sessions") or {}
    out = []
    for a in sorted(runs, key=lambda a: a.date):
        if ((sessions.get(a.date) or {}).get("type")) in ("TEST", "RACE"):
            continue
        q_km, q_sec = quality_volume([a], bands)
        when = f"{a.date[5:7]}/{a.date[8:10]}"
        for cls in ("T", "I", "R", "M"):
            cap = caps.get(f"{cls}_pct_of_week")
            if cap is None:
                continue
            limit = week_km * cap / 100
            if q_km[cls] > limit * (1 + CAP_TOLERANCE):
                out.append(f"{cls} 單堂 {q_km[cls]:.1f}km（{when}）超過週量的 {cap:g}%（{limit:.1f}km）")
        m_max = caps.get("M_max_minutes")
        if m_max and q_sec["M"] / 60 > m_max * (1 + CAP_TOLERANCE):
            out.append(f"M 單堂 {q_sec['M'] / 60:.0f} 分（{when}）超過 {m_max:g} 分")
    return out


def build_weeks(plan, activities, wellness, date_from, date_to, today):
    caps = (plan.get("rules", {}).get("quality_caps") or {})
    long_rules = (plan.get("rules", {}).get("long_run") or {})
    bands = build_pace_bands(plan.get("paces") or {})
    weeks = []

    for spec in plan.get("weeks", []):
        start = to_date(spec["start"])
        end = start + dt.timedelta(days=6)
        if end < date_from or start > date_to:
            continue

        in_week = [a for a in activities if start <= to_date(a.date) <= end]
        runs = [a for a in in_week if a.is_run]
        km = sum(a.km for a in runs)
        by_class = {c: sum(a.km for a in runs if a.cls == c) for c in CLASS_ORDER}
        q_km, q_sec = quality_volume(runs, bands)
        load = sum(a.load or 0 for a in in_week)

        ctl_start = (wellness.get(start.isoformat()) or EMPTY_WELLNESS).ctl
        ctl_end = (wellness.get(end.isoformat()) or EMPTY_WELLNESS).ctl
        if ctl_end is None:
            for offset in range(1, 7):
                probe = wellness.get((end - dt.timedelta(days=offset)).isoformat())
                if probe and probe.ctl is not None:
                    ctl_end = probe.ctl
                    break

        warnings = []
        # 進行中的週用目標跑量當分母，不然週一跑完一堂節奏課就是「佔 60%」
        in_progress = start <= today <= end
        denom = max(km, spec.get("target_km") or 0) if in_progress else km
        warnings += session_cap_warnings(runs, plan, caps, bands, denom)
        if km > 0:
            long_cap = long_rules.get("max_pct_of_week")
            if long_cap is not None:
                pct = by_class.get("L", 0.0) / km * 100
                if pct > long_cap:
                    warnings.append(f"長跑佔 {pct:.0f}%（上限 {long_cap:.0f}%）")

        long_max_min = long_rules.get("max_minutes_half_marathon")
        if long_max_min:
            for a in runs:
                if a.cls == "L" and a.seconds / 60 > long_max_min:
                    warnings.append(f"長跑 {a.seconds / 60:.0f} 分（上限 {long_max_min:.0f} 分）")
                    break

        (plan_count, plan_km, done_count, done_km,
         missed, extra) = week_plan_stats(plan, start, end, runs, today)

        weeks.append(Week(
            n=spec.get("n"),
            start=start.isoformat(),
            end=end.isoformat(),
            phase=spec.get("phase") or "",
            target_km=spec.get("target_km"),
            km=km,
            by_class=by_class,
            load=load,
            ctl_start=ctl_start,
            ctl_end=ctl_end,
            in_progress=start <= today <= end,
            warnings=warnings,
            note=spec.get("note"),
            plan_count=plan_count,
            plan_km=plan_km,
            done_count=done_count,
            done_km=done_km,
            missed=missed,
            extra=extra,
            quality_km={c: round(v, 2) for c, v in q_km.items() if v > 0},
        ))

    return check_volume_ramp(weeks, plan, date_from)


def check_volume_ramp(weeks, plan, date_from):
    """rules.volume_ramp：週跑量增幅，以及連續增量該不該安排減量週。

    只比對「兩週都完整落在抓取區間、且都不是進行中」的週；
    區間頭尾被切一半的週拿來比會得出假的增幅。
    前一週是計畫中的減量週時，改跟減量前那週比——W4 減量 27km 之後
    W5 回到 43km 是照計畫走，不是 +58% 暴增。
    """
    ramp = ((plan.get("rules") or {}).get("volume_ramp") or {})
    max_pct = ramp.get("weekly_increase_pct_max")
    normal_pct = ramp.get("weekly_increase_pct_normal")
    note = (ramp.get("note") or "").strip()

    def complete(w):
        return w is not None and to_date(w.start) >= date_from and not w.in_progress

    def is_deload(w):
        return any(p in (w.phase or "") for p in DELOAD_PHASES)

    streak = 0
    for i, w in enumerate(weeks):
        prev = weeks[i - 1] if i else None
        if prev is not None and is_deload(prev) and not is_deload(w):
            earlier = [p for p in weeks[:i - 1] if not is_deload(p)]
            prev = earlier[-1] if earlier else None
            streak = 0                              # 減量週之後重新起算
        if not (complete(w) and complete(prev) and prev.km > 0):
            streak = 0
            continue

        pct = (w.km - prev.km) / prev.km * 100
        if max_pct is not None and pct > max_pct:
            limits = (f"（建議 {normal_pct:g}%，上限 {max_pct:g}%）" if normal_pct
                      else f"（上限 {max_pct:g}%）")
            base = "較前一週" if prev is weeks[i - 1] else f"較減量前的 W{prev.n}"
            w.warnings.append(f"週量 +{pct:.0f}% {base}{limits}")

        streak = streak + 1 if pct > 0 else 0
        if streak >= 3:
            w.warnings.append(f"連續 {streak} 週增量" + (f" — {note}" if note else ""))
    return weeks


def build_days(activities, wellness, date_from, date_to):
    days = []
    cursor = date_to
    while cursor >= date_from:
        key = cursor.isoformat()
        acts = [a for a in activities if a.date == key]
        well = wellness.get(key)
        if acts or well:
            days.append(Day(
                date=key,
                weekday=WEEKDAY_ZH[cursor.weekday()],
                activities=sorted(acts, key=lambda a: (not a.is_run, -a.km)),
                wellness=well,
            ))
        cursor -= dt.timedelta(days=1)
    return days


# ---------- 課表 vs 實際 ----------

# 強度由重到輕，用來判斷「強度高於／低於處方」
CLASS_RANK = {"R": 1, "I": 2, "TEST": 2, "T": 3, "M": 4, "L": 5, "E": 6}

KM_TOLERANCE = 0.2      # 距離差幾成以內算「照做」


def sessions_span(plan):
    """plan.json sessions 涵蓋的日期範圍。

    範圍外不做對照——沒排課的日子本來就沒有「偏離」可言，
    不然 08/26 之前整份都會在喊「課表未排」。
    """
    keys = sorted(plan.get("sessions") or {})
    return (to_date(keys[0]), to_date(keys[-1])) if keys else None


def compare_day(day, session):
    """課表 vs 實際。回傳 (處方強度, 處方距離, 處方敘述, 差異描述)。

    照著做的話差異是 None，不會印出來。課挪到別天、多練、少練都會被抓到。
    """
    runs = [a for a in day.activities if a.is_run]
    actual_km = sum(a.km for a in runs)
    actual_cls = max(runs, key=lambda a: a.km).cls if runs else None

    ptype = (session or {}).get("type")
    pkm = (session or {}).get("km")
    pdesc = (session or {}).get("desc")
    km_text = f" {pkm:g}km" if pkm else ""

    def delta_word():
        if not pkm:
            return ""
        delta = actual_km - pkm
        return f"多 {delta:.1f}km" if delta >= 0 else f"少 {-delta:.1f}km"

    # 課表沒排，或排的是休息
    if not ptype or ptype == "OFF":
        if runs:
            return ptype, pkm, pdesc, f"課表未排 ／ 實際 {actual_cls} {actual_km:.1f}km — 額外訓練"
        return ptype, pkm, pdesc, None

    # 排了課但那天沒跑
    if not runs:
        return ptype, pkm, pdesc, f"處方 {ptype}{km_text} — 未執行"

    # 強度跟處方不同
    if ptype != actual_cls:
        bits = []
        pr, ar = CLASS_RANK.get(ptype), CLASS_RANK.get(actual_cls)
        if pr and ar and pr != ar:
            bits.append("強度高於處方" if ar < pr else "強度低於處方")
        if delta_word():
            bits.append(delta_word())
        tail = f" — {'，'.join(bits)}" if bits else ""
        return ptype, pkm, pdesc, f"處方 {ptype}{km_text} ／ 實際 {actual_cls} {actual_km:.1f}km{tail}"

    # 強度一樣，只看距離差多少
    if pkm and abs(actual_km - pkm) / pkm > KM_TOLERANCE:
        return ptype, pkm, pdesc, f"處方 {ptype}{km_text} ／ 實際 {actual_km:.1f}km — {delta_word()}"

    return ptype, pkm, pdesc, None


def annotate_days(days, plan):
    sessions = plan.get("sessions") or {}
    span = sessions_span(plan)
    for day in days:
        if not span or not (span[0] <= to_date(day.date) <= span[1]):
            continue
        (day.plan_type, day.plan_km,
         day.plan_desc, day.deviation) = compare_day(day, sessions.get(day.date))
    return days


def week_plan_stats(plan, start, end, runs, today):
    """一週的課表執行狀況：排幾課、做幾課、漏了哪些、哪些是額外加的。

    還沒到的日子不算未執行——本週進行中的時候，週五的課不該被標成漏掉。
    """
    sessions = plan.get("sessions") or {}
    span = sessions_span(plan)
    days = [start + dt.timedelta(days=i) for i in range(7)]
    in_span = [d for d in days if span and span[0] <= d <= span[1]]
    if not in_span:
        return 0, 0.0, 0, 0.0, [], []

    planned = {d: sessions[d.isoformat()] for d in in_span
               if (sessions.get(d.isoformat()) or {}).get("type") not in (None, "OFF")}
    ran = {to_date(a.date) for a in runs}
    missed = [f"{d.month:02d}/{d.day:02d} {s['type']}"
              for d, s in sorted(planned.items()) if d not in ran and d <= today]
    extra = [f"{d.month:02d}/{d.day:02d}"
             for d in sorted(ran) if d in in_span and d not in planned]
    plan_km = sum(s.get("km") or 0 for s in planned.values())
    done_days = [d for d in ran if d in in_span]
    done_km = sum(a.km for a in runs if to_date(a.date) in done_days)
    return len(planned), plan_km, len(done_days), done_km, missed, extra


# ---------- hrv_decision ----------

_HRV_NEEDLES = [
    ("prev_quality", "前一天有質量課"),
    ("streak", "連續 2-3 天"),
    ("streak7", "7 日平均"),
    ("rhr", "靜止心率升高"),
    ("illness", "喉嚨痛"),
]


def _hrv_rules(plan):
    """把 plan.json 的 hrv_decision 依關鍵字對到檢查代號，措辭與 level 都用 plan 的。"""
    out = {}
    for rule in ((plan.get("rules") or {}).get("hrv_decision") or []):
        for key, needle in _HRV_NEEDLES:
            if needle in (rule.get("when") or ""):
                out[key] = rule
    return out


def annotate_hrv(days, wellness, plan):
    """rules.hrv_decision：HRV 低於基線時，該不該影響今天的訓練。

    由重到輕只報最嚴重的一條：身體不適 > 靜止心率同時升高 > 7 日平均連續偏低
    > 連續數日偏低 > 前一天有質量課（正常）。
    HRV 沒低於基線的日子完全不出聲。
    """
    rules = _hrv_rules(plan)
    baseline = (plan.get("athlete") or {}).get("hrv_baseline") or []
    if not rules or not baseline:
        return days
    floor = baseline[0]

    # 連續低於基線的天數。缺資料的日子把連續計數歸零，寧可漏報也不要誤報。
    below, below7 = {}, {}
    streak = streak7 = 0
    for date in sorted(wellness):
        w = wellness[date]
        streak = streak + 1 if (w.hrv is not None and w.hrv < floor) else 0
        streak7 = streak7 + 1 if (w.hrv7 is not None and w.hrv7 < floor) else 0
        below[date], below7[date] = streak, streak7

    by_date = {day.date: day for day in days}
    for day in days:
        w = wellness.get(day.date)
        if not w or w.hrv is None or w.hrv >= floor:
            continue

        prev = by_date.get((to_date(day.date) - dt.timedelta(days=1)).isoformat())
        prev_quality = bool(prev and any(a.cls in QUALITY_CLASSES for a in prev.activities))
        rise = (w.resting_hr - w.rhr7) if (w.resting_hr is not None
                                           and w.rhr7 is not None) else None
        state = f"HRV {w.hrv:.0f} 低於基線 {floor}"

        if w.illness and "illness" in rules:
            key, detail = "illness", f"{state}，且有身體不適紀錄（{w.illness}）"
        elif rise is not None and rise >= HRV_RHR_RISE_BPM and "rhr" in rules:
            key, detail = "rhr", f"{state}，靜止心率 {w.resting_hr} 較 7 日均高 {rise:.0f} bpm"
        elif below7.get(day.date, 0) >= HRV_STREAK7_DAYS and "streak7" in rules:
            key = "streak7"
            detail = (f"HRV 7 日平均 {w.hrv7:.1f} 連續 {below7[day.date]} 天"
                      f"低於基線下緣 {floor}")
        elif below.get(day.date, 0) >= HRV_STREAK_DAYS and "streak" in rules:
            key, detail = "streak", f"{state}，已連續 {below[day.date]} 天"
        elif prev_quality and "prev_quality" in rules:
            key, detail = "prev_quality", f"{state}，但前一天有質量課"
        else:
            day.hrv_level, day.hrv_note = "info", f"{state}（單日，前一天無質量課）"
            continue

        rule = rules[key]
        action = (rule.get("action") or "").strip()
        day.hrv_key = key
        day.hrv_level = rule.get("level") or "warn"
        day.hrv_note = f"{detail} — {action}" if action else detail

    # 7 日平均連續偏低會一路持續好幾天，每天都印同一句話沒有意義。
    # 只在剛跨過門檻那天報一次，現在的狀況改放趨勢區塊。
    for day in days:
        day.hrv_repeat = (day.hrv_key == "streak7"
                          and below7.get(day.date, 0) > HRV_STREAK7_DAYS)
    return days


# ---------- 今日狀態 ----------

LIGHT_ORDER = {"gray": 0, "green": 1, "yellow": 2, "red": 3}
QUALITY_TYPES = {"T", "I", "R", "M", "TEST"}
TYPE_LABEL = {"E": "輕鬆跑", "M": "馬配", "T": "節奏", "I": "間歇", "R": "加速跑",
              "L": "長跑", "TEST": "測驗", "OFF": "休息"}


def _hrv_action(plan, needle):
    for rule in ((plan.get("rules") or {}).get("hrv_decision") or []):
        if needle in (rule.get("when") or ""):
            return (rule.get("action") or "").strip()
    return ""


def _races(plan):
    return {r["date"]: r.get("name") or "比賽" for r in plan.get("races", []) if r.get("date")}


def describe_session(session, race=None):
    """一天的課表寫成一行字：「輕鬆跑 5km — E 5km（可休息）…」。"""
    if race:
        return f"比賽：{race}" + (f" — {session['desc']}" if session and session.get("desc") else "")
    if not session:
        return "課表沒排"
    stype = session.get("type")
    desc = session.get("desc") or ""
    if stype == "OFF":
        return desc if desc else "休息"
    km = f" {session['km']:g}km" if session.get("km") else ""
    return f"{TYPE_LABEL.get(stype, stype or '')}{km}" + (f" — {desc}" if desc else "")


def build_schedule(plan, today):
    """網頁的「明日課表」與「接下來的課表」。

    今天、明天是瀏覽器依打開當下的台灣日期決定的（網頁可能是前一晚產生的）。
    """
    # 從「前一天所在那週的週一」開始：週的課表合計要算整週，網頁晚一天更新也不會少
    yesterday = today - dt.timedelta(days=1)
    start = (yesterday - dt.timedelta(days=yesterday.weekday())).isoformat()
    sessions = {d: v for d, v in sorted((plan.get("sessions") or {}).items()) if d >= start}
    weeks = []
    for w in plan.get("weeks", []):
        week_start = to_date(w["start"])
        week_end = week_start + dt.timedelta(days=6)
        if week_end.isoformat() < start:
            continue
        weeks.append({
            "n": w.get("n"), "start": week_start.isoformat(), "end": week_end.isoformat(),
            "phase": w.get("phase") or "", "target_km": w.get("target_km"),
            "long_run_min": w.get("long_run_min"), "note": w.get("note"), "key": w.get("key"),
        })
    return {"sessions": sessions, "weeks": weeks, "races": _races(plan)}


def _e_pace(plan):
    e_spec = (plan.get("paces") or {}).get("E") or {}
    return e_spec.get("target") or "-".join(x for x in (e_spec.get("min"), e_spec.get("max")) if x)


def build_today_rules(plan, today):
    """給網頁的「今日狀態」即時重算用。

    網頁打開時會直接向 Intervals.icu 抓當天的晨間資料，用這份規則在瀏覽器裡重算一次，
    所以不必等 GitHub 排程重新產生網頁。門檻數字全部從這裡來，跟 build_today 同一份；
    判斷順序寫在 web/template.html 的 computeToday()，改規則時兩邊要一起改。
    """
    sessions = plan.get("sessions") or {}
    lo, hi = (today - dt.timedelta(days=7)).isoformat(), (today + dt.timedelta(days=28)).isoformat()
    return {
        "hrv_baseline": (plan.get("athlete") or {}).get("hrv_baseline") or [],
        "rhr_red": HRV_RHR_RISE_BPM,
        "rhr_yellow": RHR_YELLOW_BPM,
        "hrv_streak_days": HRV_STREAK_DAYS,
        "hrv_streak7_days": HRV_STREAK7_DAYS,
        "subjective_bad": SUBJECTIVE_BAD,
        "subjective_fields": SUBJECTIVE_FIELDS,
        "illness_words": list(ILLNESS_WORDS),
        "quality_types": sorted(QUALITY_TYPES),
        "quality_classes": sorted(QUALITY_CLASSES),
        "run_types": sorted(RUN_TYPES),
        "type_label": TYPE_LABEL,
        "e_pace": _e_pace(plan),
        "actions": {
            "prev_quality": _hrv_action(plan, "前一天有質量課"),
            "streak": _hrv_action(plan, "連續 2-3 天"),
            "streak7": _hrv_action(plan, "7 日平均"),
            "rhr": _hrv_action(plan, "靜止心率升高"),
            "illness": _hrv_action(plan, "喉嚨痛"),
        },
        "races": _races(plan),
        "sessions": {d: v for d, v in sessions.items() if lo <= d <= hi},
    }


def build_today(plan, days, wellness, today):
    """首頁那一行：今天練不練、練什麼。

    三個訊號各自亮燈，總燈號照 plan.json 的 hrv_decision 走：
      紅（休息）：主觀有 4 分 / 傷病 ≥ 3，或 HRV 低於基線且靜止心率高 5 下以上
      黃（降級）：HRV 連續 2 天低於基線，或靜止心率單獨高 5 下，或主觀有 3 分
      綠：其他。HRV 單日偏低只在那個訊號上亮黃，不影響總燈號——
          plan.json 說前一天有質量課時這是正常的，沒有的話也只是「留意」。
    「HRV 7 日平均連續偏低」是一週的減量決定，不是今天的，另外寫在 week_note。
    """
    key = today.isoformat()
    well = wellness.get(key)
    has_data = lambda w: w is not None and (w.hrv is not None or w.resting_hr is not None)
    data_date = key if has_data(well) else None
    if data_date is None:                        # 今天的晨間資料還沒同步
        for back in range(1, 4):
            probe = (today - dt.timedelta(days=back)).isoformat()
            if has_data(wellness.get(probe)):
                data_date, well = probe, wellness[probe]
                break

    session = (plan.get("sessions") or {}).get(key)
    signals, reasons = [], []

    # 靜止心率 vs 前 7 天平均（不含當天，不然當天的高點會把平均一起拉高）
    rhr_rise = None
    if well and well.resting_hr is not None:
        d0 = to_date(data_date)
        prev = [wellness[x].resting_hr for x in
                ((d0 - dt.timedelta(days=i)).isoformat() for i in range(1, 8))
                if x in wellness and wellness[x].resting_hr is not None]
        if prev:
            avg = sum(prev) / len(prev)
            rhr_rise = well.resting_hr - avg
            light = ("red" if rhr_rise >= HRV_RHR_RISE_BPM else
                     "yellow" if rhr_rise >= RHR_YELLOW_BPM else "green")
            signals.append(Signal("靜止心率", f"{well.resting_hr}", f"7 日 {avg:.0f}，{rhr_rise:+.0f}", light))
        else:
            signals.append(Signal("靜止心率", f"{well.resting_hr}", "7 日平均不足", "gray"))
    else:
        signals.append(Signal("靜止心率", "--", "沒有資料", "gray"))

    # HRV vs plan.json 的基線
    baseline = (plan.get("athlete") or {}).get("hrv_baseline") or []
    hrv_low = hrv_streak = 0
    if well and well.hrv is not None and baseline:
        floor = baseline[0]
        d0 = to_date(data_date)
        while True:                               # 連續幾天低於基線（含今天）
            w = wellness.get((d0 - dt.timedelta(days=hrv_streak)).isoformat())
            if w and w.hrv is not None and w.hrv < floor:
                hrv_streak += 1
            else:
                break
        hrv_low = hrv_streak > 0
        light = "red" if hrv_streak >= HRV_STREAK_DAYS else "yellow" if hrv_low else "green"
        note = f"連續 {hrv_streak} 天偏低" if hrv_streak >= 2 else ""
        ref = f"基線 {baseline[0]}–{baseline[1]}" if len(baseline) > 1 else f"基線 {floor}"
        signals.append(Signal("HRV", f"{well.hrv:.0f}", ref, light, note))
    else:
        signals.append(Signal("HRV", "--", "沒有資料", "gray"))

    # 主觀分數：取最差的一項
    subj = well.subjective if well else {}
    if subj:
        worst_label, worst = max(subj.items(), key=lambda kv: kv[1])
        light = "red" if worst >= 4 else "yellow" if worst >= SUBJECTIVE_BAD else "green"
        injury = subj.get("傷病", 0)
        if injury >= SUBJECTIVE_BAD:
            light = "red"
        detail = " · ".join(f"{k} {v:g}" for k, v in subj.items())
        signals.append(Signal("主觀", f"{worst_label} {worst:g}", detail, light))
    else:
        signals.append(Signal("主觀", "未填", "在 intervals.icu 記疲勞、痠痛就會納入", "gray"))
    subj_light = signals[-1].light

    # 總燈號
    illness = bool(well and any(w in (well.illness or "") for w in ILLNESS_WORDS))
    if subj_light == "red":
        light = "red"; reasons.append("主觀分數有 4 分或傷病")
    elif illness and hrv_low:
        light = "red"; reasons.append(f"HRV 低於基線，且每日記錄提到身體不適 — {_hrv_action(plan, '喉嚨痛') or '停練'}")
    elif hrv_low and rhr_rise is not None and rhr_rise >= HRV_RHR_RISE_BPM:
        light = "red"; reasons.append(f"HRV 低於基線，同時靜止心率高 {rhr_rise:.0f} 下 — "
                                      f"{_hrv_action(plan, '靜止心率升高') or '休息'}")
    elif hrv_streak >= HRV_STREAK_DAYS:
        light = "yellow"; reasons.append(f"HRV 連續 {hrv_streak} 天低於基線 — "
                                         f"{_hrv_action(plan, '連續 2-3 天') or '質量課降級'}")
    elif rhr_rise is not None and rhr_rise >= HRV_RHR_RISE_BPM:
        light = "yellow"; reasons.append(f"靜止心率比平常高 {rhr_rise:.0f} 下")
    elif illness:          # plan.json 只寫了「HRV 低且生病 → 停練」；HRV 正常時保守一點，降級
        light = "yellow"; reasons.append("每日記錄提到身體不適，質量課先降級")
    elif subj_light == "yellow":
        light = "yellow"; reasons.append("主觀分數有 3 分")
    elif all(sg.light == "gray" for sg in signals):
        light = "gray"; reasons.append("沒有晨間資料")
    else:
        light = "green"
        if hrv_low:
            prev_day = next((d for d in days if d.date == (to_date(data_date) - dt.timedelta(days=1)).isoformat()), None)
            if prev_day and any(a.cls in QUALITY_CLASSES for a in prev_day.activities):
                reasons.append(f"HRV 單日偏低，但前一天有質量課 — {_hrv_action(plan, '前一天有質量課') or '正常'}")
            else:
                reasons.append("HRV 單日偏低，明天再看一次")

    # 處方
    stype = (session or {}).get("type")
    label = TYPE_LABEL.get(stype, stype or "")
    km_text = f" {session['km']:g}km" if session and session.get("km") else ""
    desc = f" — {session['desc']}" if session and session.get("desc") else ""
    e_pace = _e_pace(plan)
    if light == "red":
        headline = "休息"
        prescription = "今天休息" + (f"，課表的{label}課往後推" if stype and stype != "OFF" else "")
    elif light == "yellow":
        headline = "降級"
        if stype in QUALITY_TYPES:
            prescription = f"{label}課改成 E 40 分（{e_pace}），或整堂往後推一天"
        elif stype == "OFF":
            prescription = "照課表休息"
        elif stype:
            prescription = f"照課表{label}{km_text}，強度壓在 E（{e_pace}）"
        else:
            prescription = f"今天課表沒排；要練就只做 E（{e_pace}），質量課往後推"
    elif light == "green" and stype == "OFF":
        headline = "休息日"
        prescription = (session.get("desc") or "照課表休息") if session else "照課表休息"
    elif light == "green":
        headline = "照課表練"
        prescription = f"{label}{km_text}{desc}" if stype else "今天課表沒排，照你的計畫練"
    else:
        headline = "沒有資料"
        prescription = (f"{label}{km_text}{desc}" if stype else "今天課表沒排") + "（沒有晨間資料可以判斷）"

    # 比賽不能降級也不能延期：處方照課表，燈號和理由留著給你自己決定配速策略
    race = _races(plan).get(key)
    if race:
        headline = "比賽日"
        prescription = (session or {}).get("desc") or race
        reasons = [r.split(" — ")[0] for r in reasons]      # 「降級、往後推」不適用比賽，只留觀察

    week_note = None
    if baseline and well and well.hrv7 is not None and well.hrv7 < baseline[0]:
        streak7 = 0
        d0 = to_date(data_date)
        while True:
            w = wellness.get((d0 - dt.timedelta(days=streak7)).isoformat())
            if w and w.hrv7 is not None and w.hrv7 < baseline[0]:
                streak7 += 1
            else:
                break
        if streak7 >= HRV_STREAK7_DAYS:
            week_note = (f"HRV 7 日平均 {well.hrv7:.1f} 已連續 {streak7} 天低於基線 {baseline[0]} — "
                         f"{_hrv_action(plan, '7 日平均') or '考慮減量'}")

    return Today(
        date=key, weekday=WEEKDAY_ZH[today.weekday()], data_date=data_date,
        light=light, headline=headline, prescription=prescription, reasons=reasons,
        signals=signals, session=session, week_note=week_note,
    )


def build_trends(plan, activities, wellness, date_from, date_to):
    start = date_from.isoformat()
    runs = [a for a in activities if a.is_run and a.pace_s and a.date >= start]

    # E 配速@固定心率 — 判斷有氧體能進步最可靠的指標。
    # 這個帳號的 E 跑均心多落在 137-145，固定 140-150 常常只抓到一兩點，
    # 所以點數不足時自動放寬，實際用的窗會印在標題上。
    window = E_HR_WINDOW
    points = []
    for widen in (0, 5, 10):
        lo, hi = E_HR_WINDOW[0] - widen, E_HR_WINDOW[1] + widen
        points = [(a.date, a.pace_s) for a in runs
                  if a.cls == "E" and a.km >= E_TREND_MIN_KM
                  and a.hr_avg and lo <= a.hr_avg <= hi]
        window = (lo, hi)
        if len(points) >= E_TREND_MIN_POINTS:
            break
    points.sort()
    points = points[-4:]

    checkpoints = [d for d in (date_to - dt.timedelta(days=14),
                               date_to - dt.timedelta(days=7),
                               date_to) if d >= date_from]
    hrv7, rhr7 = [], []
    for day in checkpoints:
        row = None
        for offset in range(0, 4):          # 當天沒資料就往前找幾天
            row = row or wellness.get((day - dt.timedelta(days=offset)).isoformat())
        hrv7.append((day.isoformat(), row.hrv7 if row else None))
        rhr7.append((day.isoformat(), row.rhr7 if row else None))

    latest = None
    for day in sorted(wellness, reverse=True):
        if wellness[day].ctl is not None:
            latest = wellness[day]
            break

    longest = None
    if runs:
        top = max(runs, key=lambda a: a.km)
        longest = (top.km, top.seconds / 60, top.date)

    return Trends(
        e_hr_window=window,
        e_pace_points=points,
        hrv7_points=hrv7,
        hrv_baseline=(plan.get("athlete") or {}).get("hrv_baseline"),
        rhr7_points=rhr7,
        ctl=latest.ctl if latest else None,
        atl=latest.atl if latest else None,
        tsb=latest.tsb if latest else None,
        longest=longest,
    )


def build(plan, raw_activities, intervals, raw_wellness, date_from, date_to,
          today=None, strict_class=False) -> Digest:
    today = today or dt.date.today()
    paces = plan.get("paces", {})
    bands = build_pace_bands(paces)
    e_pace_limit = parse_pace((paces.get("E") or {}).get("min"))

    wellness = build_wellness(raw_wellness)
    activities = build_activities(raw_activities, intervals, plan, bands, e_pace_limit, strict_class)
    weeks = build_weeks(plan, activities, wellness, date_from, date_to, today)
    days = annotate_days(build_days(activities, wellness, date_from, date_to), plan)
    days = annotate_hrv(days, wellness, plan)
    trends = build_trends(plan, activities, wellness, date_from, date_to)
    trends.hrv_status = next((d.hrv_note for d in days if d.hrv_note), None)

    races = plan.get("races", [])
    goal = next((r for r in races if "A" in (r.get("role") or "")), races[-1] if races else None)
    interim = [r for r in races if r is not goal]
    days_left = (to_date(goal["date"]) - today).days if goal else None
    current = next((w for w in weeks if w.in_progress), weeks[-1] if weeks else None)

    today_status = build_today(plan, days, wellness, today)
    today_rules = build_today_rules(plan, today)
    schedule = build_schedule(plan, today)

    return Digest(
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        goal_race=goal,
        goal_days_left=days_left,
        interim_races=interim,
        current_week=current,
        paces=paces,
        vdot=plan.get("vdot", {}),
        weeks=weeks,
        days=days,
        trends=trends,
        today=today_status,
        today_rules=today_rules,
        schedule=schedule,
    )

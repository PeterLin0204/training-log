"""摘要資料結構 → 純文字。

只有這個檔案知道輸出長什麼樣子。階段 2 要接靜態頁時，換掉這個檔案
（改成 json.dumps(asdict(digest))）即可，icu.py 與 transform.py 不用動。
"""

from __future__ import annotations

import unicodedata

from transform import CLASS_ORDER, Digest, fmt_clock, fmt_hm, fmt_pace

INDENT = " " * 6


# ---------- 全形對齊 ----------

def width(text: str) -> int:
    """中文字在終端機佔兩格，ljust 算不準，自己數。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, target: int) -> str:
    return text + " " * max(0, target - width(text))


def num(value, digits=1, dash="--"):
    if value is None:
        return dash
    rounded = round(value, digits) + 0.0      # 讓 -0.4 印成 0 而不是 -0
    return f"{abs(rounded) if rounded == 0 else rounded:.{digits}f}"


def md(iso: str) -> str:
    return f"{iso[5:7]}/{iso[8:10]}"


# ---------- 強度標籤 ----------

DEFAULT_LABELS = {
    "E": "輕鬆跑", "M": "馬配", "T": "節奏", "I": "間歇", "R": "加速跑",
    "L": "長跑", "OFF": "休息", "TEST": "測驗", "XT": "交叉訓練",
}


def class_label(cls: str, paces: dict) -> str:
    """plan.json 的 paces.*.label 有寫就用它（閾值/節奏 → 節奏），沒寫就用內建的。"""
    label = (paces.get(cls) or {}).get("label")
    if label:
        return label.split("/")[-1].split(" ")[0]
    return DEFAULT_LABELS.get(cls, cls)


# ---------- 1. 檔頭 ----------

def render_header(d: Digest) -> list[str]:
    lines = [f"=== 訓練摘要 {d.date_from} ~ {d.date_to} ==="]

    if d.goal_race:
        r = d.goal_race
        goal = []
        if r.get("goal_min"):
            pace = f" ({r['goal_pace']}/km)" if r.get("goal_pace") else ""
            goal.append(f"{r['goal_min']:g} 分{pace}")
        if d.goal_days_left is not None:
            goal.append(f"剩 {d.goal_days_left} 天")
        lines.append(f"目標　{r['name']} {md(r['date'])} · " + " · ".join(goal))

    if d.interim_races:
        parts = [f"{r['name']} {md(r['date'])} ({r.get('role', '')})" for r in d.interim_races]
        lines.append("期中　" + " · ".join(parts))

    w = d.current_week
    if w:
        target = f" / {w.target_km:g} km" if w.target_km else ""
        lines.append(f"現況　W{w.n} {w.phase}期 · 本週 {w.km:.1f}{target}")

    paces = []
    for key in ("E", "M", "T", "I", "R"):
        spec = d.paces.get(key)
        if not spec:
            continue
        if spec.get("target"):
            paces.append(f"{key} {spec['target']}")
        elif spec.get("min") and spec.get("max"):
            paces.append(f"{key} {spec['min']}-{spec['max']}")
    vdot = d.vdot.get("current")
    tail = f"  (VDOT {vdot:g})" if vdot else ""
    lines.append("配速　" + " | ".join(paces) + tail)
    return lines


# ---------- 2. 週統計 ----------

def render_weeks(d: Digest) -> list[str]:
    lines = ["", "--- 週統計 ---"]
    if not d.weeks:
        lines.append("（這段期間沒有對應到 plan.json 的週次）")
        return lines

    for w in d.weeks:
        parts = [f"{c} {w.by_class.get(c, 0):g}" if w.by_class.get(c, 0) == 0
                 else f"{c} {w.by_class[c]:.1f}"
                 for c in CLASS_ORDER if w.by_class.get(c, 0) > 0]
        if w.by_class.get("L", 0) == 0:
            parts.append("L 0")
        breakdown = " / ".join(parts) or "—"

        head = f"W{w.n:<2} {md(w.start)}-{md(w.end)}  {w.km:>5.1f}km  {pad(breakdown, 34)}"
        if w.in_progress:
            target = f"（目標 {w.target_km:g}km）" if w.target_km else ""
            lines.append(f"{head}進行中{target}")
        else:
            ctl = "--"
            if w.ctl_start is not None or w.ctl_end is not None:
                ctl = f"{num(w.ctl_start, 0)}→{num(w.ctl_end, 0)}"
            lines.append(f"{head}負荷 {w.load:<4} CTL {ctl}")

        if w.plan_count or w.extra:
            bits = [f"處方 {w.plan_count} 課 {w.plan_km:g}km ／ 實際 {w.done_count} 課 {w.done_km:.1f}km"]
            if w.missed:
                bits.append("未執行 " + "、".join(w.missed))
            if w.extra:
                bits.append("額外 " + "、".join(w.extra))
            lines.append("    課表  " + " · ".join(bits))

        for warning in w.warnings:
            lines.append(f"    ⚠ {warning}")
    return lines


# ---------- 3. 每日明細 ----------

def render_activity(a, paces: dict) -> list[str]:
    lines = []
    hr_avg = a.hr_avg if a.hr_avg else "--"
    hr_max = a.hr_max if a.hr_max else "--"
    load = a.load if a.load is not None else "--"

    if a.is_run:
        head = (f"[{a.cls}] {class_label(a.cls, paces)} {a.km:.1f}km "
                f"{fmt_pace(a.pace_s)}/km 均心{hr_avg} 最高{hr_max} 負荷{load}")
    else:
        head = (f"[XT] {a.name or a.sport} {a.seconds / 60:.0f} 分 "
                f"均心{hr_avg} 最高{hr_max} 負荷{load}")
    lines.append(head)

    if a.work_laps:
        for i, lap in enumerate(a.work_laps):
            if lap.hr_avg and lap.hr_max and lap.hr_max != lap.hr_avg:
                hr = f"HR {lap.hr_avg}→{lap.hr_max}"
            elif lap.hr_avg:
                hr = f"HR {lap.hr_avg}"
            else:
                hr = "HR --"
            prefix = "laps  " if i == 0 else " " * 6
            lines.append(f"{INDENT}{prefix}{fmt_clock(lap.seconds)}  "
                         f"{lap.meters / 1000:.2f}km  {fmt_pace(lap.pace_s)}  {hr}")
    return lines


def render_days(d: Digest) -> list[str]:
    lines = ["", "--- 每日明細 ---"]
    for day in d.days:
        stamp = f"{md(day.date)} {day.weekday} "
        if day.activities:
            blocks = []
            for a in day.activities:
                blocks.extend(render_activity(a, d.paces))
            lines.append(stamp + blocks[0])
            lines.extend(b if b.startswith(INDENT) else INDENT + b for b in blocks[1:])
        else:
            lines.append(f"{stamp}[--] 休息")

        if day.deviation:                       # 照著課表做就不印
            lines.append(f"{INDENT}對照  {day.deviation}")
            if day.plan_desc:
                lines.append(f"{INDENT}      {day.plan_desc}")

        w = day.wellness
        if w:
            hrv = f"{w.hrv:.0f}" if w.hrv is not None else "--"
            hrv7 = f"{w.hrv7:.0f}" if w.hrv7 is not None else "--"
            rhr = w.resting_hr if w.resting_hr is not None else "--"
            morning = f"{INDENT}晨間  HRV {hrv} (7日 {hrv7}) · RHR {rhr} · 睡 {fmt_hm(w.sleep_secs)}"
            if w.sleep_score is not None:
                morning += f" · 睡分 {w.sleep_score:.0f}"
            lines.append(morning)

            if w.ctl is not None or w.atl is not None:
                state = (f"{INDENT}狀態  CTL {num(w.ctl, 0)} ATL {num(w.atl, 0)} "
                         f"TSB {num(w.tsb, 0)}")
                if any(a.is_run for a in day.activities):   # 休息日不印脫鉤
                    dec = next((a.decoupling for a in day.activities
                                if a.decoupling is not None), None)
                    state += f" · 脫鉤 {f'{dec:.1f}%' if dec is not None else '--'}"
                lines.append(state)

        if day.hrv_note and not day.hrv_repeat:
            mark = "⚠ " if day.hrv_level in ("warn", "alert") else "註記  "
            lines.append(f"{INDENT}{mark}{day.hrv_note}")

        for a in day.activities:
            if a.description:
                for i, chunk in enumerate(a.description.splitlines()):
                    label = "筆記  " if i == 0 else " " * 6
                    lines.append(f"{INDENT}{label}{chunk}")
        for a in day.activities:
            for flag in a.flags:
                lines.append(f"{INDENT}⚠ {flag}")
        lines.append("")
    return lines


# ---------- 4. 趨勢 ----------

def render_trends(d: Digest) -> list[str]:
    t = d.trends
    lines = ["--- 趨勢 ---"]
    label_w = 22

    lo, hi = t.e_hr_window
    if t.e_pace_points:
        series = "  →  ".join(f"{md(date)} {fmt_pace(pace)}" for date, pace in t.e_pace_points)
    else:
        series = "--（區間內沒有落在此心率窗的 E 跑）"
    lines.append(pad(f"E 配速@心率{lo}-{hi}", label_w) + series)

    baseline = t.hrv_baseline
    tail = f"  （基線 {baseline[0]}-{baseline[1]}）" if baseline and len(baseline) == 2 else ""
    hrv_series = " → ".join(num(v, 1) for _d, v in t.hrv7_points) or "--"
    lines.append(pad("HRV 7 日平均", label_w) + hrv_series + tail)

    if t.hrv_status:
        lines.append(pad("現況判讀", label_w) + t.hrv_status)

    rhr_series = " → ".join(num(v, 0) for _d, v in t.rhr7_points) or "--"
    lines.append(pad("靜止心率", label_w) + rhr_series)

    lines.append(pad("CTL / TSB", label_w) + f"{num(t.ctl, 0)} / {num(t.tsb, 0)}")

    if t.longest:
        km, minutes, date = t.longest
        lines.append(pad("最長單次", label_w) + f"{km:.1f}km / {minutes:.0f} 分（{md(date)}）")
    else:
        lines.append(pad("最長單次", label_w) + "--")
    return lines


def render(d: Digest) -> str:
    lines = []
    lines += render_header(d)
    lines += render_weeks(d)
    lines += render_days(d)
    lines += render_trends(d)
    return "\n".join(lines).rstrip() + "\n"

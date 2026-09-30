"""訓練摘要 — 進入點 + CLI。

    python digest.py                    # 預設近 4 週，純文字印到 stdout
    python digest.py --weeks 8
    python digest.py --from 2026-08-10 --to 2026-08-26
    python digest.py --out digest.txt
    python digest.py --format html --out docs/index.html   # 互動網頁；.env 有 DIGEST_PASSWORD 就會加密
    python digest.py --format json --out docs/data.json    # 純資料
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import icu
import render
import render_html
import transform

# wellness 的 7 日滾動平均需要區間開始前幾天的資料
WELLNESS_LOOKBACK_DAYS = 7


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="digest.py",
        description="從 Intervals.icu 產出可直接貼進聊天視窗的訓練摘要。",
    )
    p.add_argument("--weeks", type=int, default=4, help="往回抓幾週（預設 4）")
    p.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="起始日期")
    p.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="結束日期")
    p.add_argument("--out", metavar="FILE", help="輸出檔案（預設印到 stdout）")
    p.add_argument("--format", choices=("text", "json", "html"), default="text",
                   help="text＝貼聊天用的純文字（預設）；html＝互動網頁；json＝純資料")
    enc = p.add_mutually_exclusive_group()
    enc.add_argument("--encrypt", action="store_true",
                     help="html 一定要加密；環境變數沒有 DIGEST_PASSWORD 就直接失敗（發佈用）")
    enc.add_argument("--plain", action="store_true",
                     help="html 不加密，即使有 DIGEST_PASSWORD（只在本機看）")
    p.add_argument("--plan", default="plan.json", help="週期設定檔（預設 plan.json）")
    p.add_argument("--no-cache", action="store_true", help="略過 .cache/，強制重新抓")
    p.add_argument("--strict-class", action="store_true",
                   help="只用 plan.json sessions 與活動名稱關鍵字分類，關掉配速回推")
    return p.parse_args(argv)


def resolve_range(args, today: dt.date):
    if args.date_from or args.date_to:
        date_to = dt.date.fromisoformat(args.date_to) if args.date_to else today
        date_from = (dt.date.fromisoformat(args.date_from) if args.date_from
                     else date_to - dt.timedelta(days=7 * args.weeks - 1))
    else:
        date_to = today
        date_from = date_to - dt.timedelta(days=7 * args.weeks - 1)
    if date_from > date_to:
        raise SystemExit("錯誤：--from 晚於 --to")
    return date_from, date_to


def local_today(plan: dict) -> dt.date:
    """依 plan.json 的時區算「今天」。GitHub Actions 的 runner 是 UTC，
    台北早上六點跑的時候 UTC 還是前一天，直接用 date.today() 會少算一天。"""
    tz_name = (plan.get("meta") or {}).get("timezone")
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return dt.datetime.now(ZoneInfo(tz_name)).date()
        except Exception:                       # Windows 可能沒有 tzdata
            pass
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")      # Windows 主控台預設 cp950
        except AttributeError:
            pass

    args = parse_args(argv)
    icu.load_env()

    plan_path = Path(args.plan)
    if not plan_path.exists():
        raise SystemExit(f"錯誤：找不到 {plan_path}")
    plan = transform.load_plan(plan_path)

    today = local_today(plan)
    date_from, date_to = resolve_range(args, today)

    password = None if args.plain else (os.environ.get("DIGEST_PASSWORD") or None)
    if args.encrypt and not password:
        raise SystemExit("錯誤：--encrypt 需要環境變數 DIGEST_PASSWORD，拒絕產生未加密的網頁")

    try:
        client = icu.ICUClient(use_cache=not args.no_cache)
    except icu.ICUError as exc:
        raise SystemExit(f"錯誤：{exc}")

    log = lambda msg: print(msg, file=sys.stderr)
    log(f"[1/3] 抓活動 {date_from} ~ {date_to} ...")
    activities = client.activities(date_from.isoformat(), date_to.isoformat())
    log(f"      {len(activities)} 筆")

    runs = [a for a in activities if (a.get("type") or "") in transform.RUN_TYPES]
    log(f"[2/3] 抓 {len(runs)} 筆跑步的分段 ...")
    intervals = {}
    for i, a in enumerate(runs, 1):
        intervals[a["id"]] = client.intervals(a["id"])
        print(f"\r      {i}/{len(runs)}", end="", file=sys.stderr)
    print("", file=sys.stderr)

    log("[3/3] 抓健康資料 ...")
    wellness = client.wellness(
        (date_from - dt.timedelta(days=WELLNESS_LOOKBACK_DAYS)).isoformat(),
        date_to.isoformat(),
    )
    log(f"      {len(wellness)} 天\n")

    digest = transform.build(plan, activities, intervals, wellness,
                             date_from, date_to, today=today,
                             strict_class=args.strict_class)

    if args.format == "html":
        text = render_html.render(digest, password=password)
        log("網頁已加密" if password else "注意：網頁未加密（沒有 DIGEST_PASSWORD），只適合在本機看")
    elif args.format == "json":
        text = render_html.to_json(digest)
    else:
        text = render.render(digest)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        log(f"已寫入 {out}（{len(text.splitlines())} 行，{len(text.encode('utf-8')) // 1024} KB）")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

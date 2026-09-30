"""Intervals.icu API client — 認證、重試、磁碟快取。

SPEC 要求「先用 curl 確認實際回傳結構再寫解析」。以下是 2026-08-27 對
我的帳號實際打過之後確認的事實，sample/ 下有對應的原始 JSON：

  * 認證：HTTP Basic，username 固定字串 "API_KEY"，password 為個人 key。
  * GET /athlete/{id}/activities 回傳的每個物件「已經是完整活動」
    （183 個欄位，與逐筆 GET /activity/{id} 完全相同），
    所以不需要再逐筆打單筆活動端點。
  * GET /activity/{id}/intervals 回傳 dict：
      {id, analyzed, icu_intervals: [...], icu_groups: [...]}
    lap 欄位「沒有」icu_ 前綴，實際欄位名是
      distance / moving_time / elapsed_time / average_speed /
      average_heartrate / max_heartrate / type / group_id / label
  * GET /athlete/{id}/wellness 支援 ?oldest=&newest= 區間查詢，回傳 list，
    不必逐日拉。HRV 欄位名是 `hrv`（單位 ms）；`hrvSDNN` 同時存在但為 null。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

BASE_URL = "https://intervals.icu/api/v1"
CACHE_DIR = Path(".cache")
CACHE_TTL_SECONDS = 6 * 3600

# activities 端點支援 fields= 只取需要的欄位，可把 125KB 的 payload 砍到 5KB
ACTIVITY_FIELDS = [
    "id", "start_date_local", "name", "type", "distance", "moving_time",
    "elapsed_time", "average_heartrate", "max_heartrate", "icu_training_load",
    "icu_efficiency_factor", "icu_hr_zone_times", "description",
    "average_speed", "decoupling", "gap", "total_elevation_gain",
]


class ICUError(RuntimeError):
    pass


def load_env(path: str = ".env") -> None:
    """讀 .env 進 os.environ。有 python-dotenv 就用，沒有就用內建的簡易解析。

    這個專案刻意只依賴 requests，所以 dotenv 是選用而非必要。
    """
    candidates = [Path(path), Path(".env.txt")]
    target = next((p for p in candidates if p.exists()), None)
    if target is None:
        return
    if target.name != ".env":
        print(f"[warn] 找不到 .env，改讀 {target}。"
              f"建議改名為 .env，否則 .gitignore 蓋不到，API key 有被 commit 的風險。",
              file=sys.stderr)
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(target)
        return
    except ImportError:
        pass
    for line in target.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


class ICUClient:
    def __init__(self, athlete_id: str | None = None, api_key: str | None = None,
                 use_cache: bool = True, cache_ttl: int = CACHE_TTL_SECONDS):
        self.athlete_id = athlete_id or os.environ.get("ICU_ATHLETE_ID", "")
        self.api_key = api_key or os.environ.get("ICU_API_KEY", "")
        if not self.athlete_id or not self.api_key:
            raise ICUError(
                "缺少憑證。請在 .env 設定 ICU_ATHLETE_ID 與 ICU_API_KEY"
                "（Intervals.icu → Settings → Developer Settings 取得 API key）。"
            )
        self.use_cache = use_cache
        self.cache_ttl = cache_ttl
        self.session = requests.Session()
        self.session.auth = ("API_KEY", self.api_key)
        self.session.headers["Accept"] = "application/json"

    # ---------- 快取 ----------

    def _cache_path(self, url: str, params: dict | None) -> Path:
        raw = url + "?" + json.dumps(params or {}, sort_keys=True)
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        slug = url.replace(BASE_URL + "/", "").replace("/", "_")[:60]
        return CACHE_DIR / f"{slug}.{digest}.json"

    def _read_cache(self, path: Path):
        if not self.use_cache or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, path: Path, data) -> None:
        if not self.use_cache:
            return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # ---------- HTTP ----------

    def _get(self, endpoint: str, params: dict | None = None, retries: int = 3):
        url = f"{BASE_URL}{endpoint}"
        cache_path = self._cache_path(url, params)
        cached = self._read_cache(cache_path)
        if cached is not None:
            return cached

        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                data = resp.json()
                self._write_cache(cache_path, data)
                return data
            if resp.status_code in (401, 403):
                raise ICUError(f"認證失敗（HTTP {resp.status_code}）。請確認 ICU_API_KEY 與 ICU_ATHLETE_ID。")
            if resp.status_code == 404:
                raise ICUError(f"找不到資源：{url}")
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                last_error = ICUError(f"HTTP {resp.status_code}：{resp.text[:200]}")
                time.sleep(wait)
                continue
            raise ICUError(f"HTTP {resp.status_code}：{resp.text[:200]}")

        raise ICUError(f"重試 {retries} 次後仍失敗：{url}（{last_error}）")

    # ---------- 端點 ----------

    def activities(self, oldest: str, newest: str, fields: list[str] | None = None) -> list[dict]:
        """活動清單。回傳的物件已是完整活動，不需要再逐筆抓 /activity/{id}。"""
        params = {"oldest": oldest, "newest": newest}
        fields = ACTIVITY_FIELDS if fields is None else fields
        if fields:
            params["fields"] = ",".join(fields)
        data = self._get(f"/athlete/{self.athlete_id}/activities", params)
        return data if isinstance(data, list) else []

    def activity(self, activity_id: str) -> dict:
        """單筆活動。實務上用不到（activities 已含全部欄位），保留給除錯。"""
        return self._get(f"/activity/{activity_id}")

    def intervals(self, activity_id: str) -> list[dict]:
        """分段資料。回傳 icu_intervals（已驗證：欄位名無 icu_ 前綴）。"""
        try:
            data = self._get(f"/activity/{activity_id}/intervals")
        except ICUError:
            return []
        if isinstance(data, dict):
            return data.get("icu_intervals") or []
        return data if isinstance(data, list) else []

    def wellness(self, oldest: str, newest: str) -> list[dict]:
        """健康資料。已驗證支援區間查詢，回傳 list，不需逐日拉。"""
        data = self._get(f"/athlete/{self.athlete_id}/wellness",
                         {"oldest": oldest, "newest": newest})
        return data if isinstance(data, list) else []

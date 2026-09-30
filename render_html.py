"""摘要資料結構 → 單一 HTML 檔（資料內嵌，不需要伺服器，file:// 直接開也行）。

版面與互動全部在 web/template.html；這裡只負責把 Digest 轉成 JSON 塞進去。
icu.py 與 transform.py 完全不用動，這就是階段 1 分層的目的。

給了密碼的話，內嵌的是 AES-256-GCM 加密後的資料，頁面要輸入密碼才解得開。
GitHub Pages 免費版只能放公開 repo，這是讓網頁「只有自己看得到」的做法。
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path

TEMPLATE = Path(__file__).parent / "web" / "template.html"
DATA_MARKER = "/*__DIGEST_JSON__*/"
GENERATED_MARKER = "__GENERATED_AT__"

# 金鑰衍生：PBKDF2-SHA256，OWASP 2023 建議的次數。瀏覽器解一次約 0.5-2 秒，
# 之後「記住」的是衍生出來的金鑰，不會每次都算。
PBKDF2_ITERATIONS = 600_000
# 鹽固定不變，這樣每天重新產生網頁時，瀏覽器記住的金鑰仍然有效；
# 換密碼時金鑰自然對不上，頁面會重新要密碼。鹽本來就不需要保密。
SALT = b"training-log/digest/v1"


def to_json(digest) -> str:
    """dataclass → JSON 字串。內嵌進 <script> 時要把 </ 斷開，不然遇到活動名稱
    含有 </script> 之類的字串會提早結束標籤。"""
    payload = asdict(digest) if is_dataclass(digest) else digest
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/")


def encrypt(plaintext: str, password: str) -> str:
    """回傳頁面上 gate() 看得懂的加密信封（JSON 字串）。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), SALT, PBKDF2_ITERATIONS, dklen=32)
    iv = os.urandom(12)
    sealed = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None)   # 密文後面接 16 bytes tag，WebCrypto 同格式
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return json.dumps({"enc": "aes-256-gcm", "kdf": "pbkdf2-sha256", "iter": PBKDF2_ITERATIONS,
                       "salt": b64(SALT), "iv": b64(iv), "data": b64(sealed)}, separators=(",", ":"))


def render(digest, generated_at: dt.datetime | None = None, password: str | None = None) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if DATA_MARKER not in template:
        raise RuntimeError(f"{TEMPLATE} 裡找不到 {DATA_MARKER}")
    stamp = (generated_at or dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
    if password:
        payload = asdict(digest) if is_dataclass(digest) else digest
        data = encrypt(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), password)
    else:
        data = to_json(digest)
    return (template
            .replace(DATA_MARKER, data)
            .replace(GENERATED_MARKER, stamp))

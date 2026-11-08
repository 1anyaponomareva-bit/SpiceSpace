"""Mini App API accepts only Telegram-signed initData; admin needs a secret key."""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qsl, urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datetime import datetime  # noqa: E402

_NAMES = {"_auth_telegram_id", "_extract_init_data", "_validate_init_data", "_is_admin_request"}
TOKEN = "123456:TEST_TOKEN_FOR_UNIT_TESTS_ONLY_abcdef"
ADMIN = 8412438788


class HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(detail)
        self.status_code = status_code


def _load() -> dict:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in _NAMES]
    ns = {
        "os": os, "hmac": hmac, "hashlib": hashlib, "json": json, "parse_qsl": parse_qsl,
        "datetime": datetime, "HTTPException": HTTPException, "Request": object,
        "ADMIN_TELEGRAM_ID": ADMIN, "log": SimpleNamespace(warning=lambda *a, **k: None, error=lambda *a, **k: None),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), ns)
    return ns


def _signed_init_data(user_id: int, *, with_signature: bool, signature_in_check: bool) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAE",
        "user": json.dumps({"id": user_id, "first_name": "Аня"}, ensure_ascii=False),
    }
    if with_signature:
        fields["signature"] = "sig"
    check = dict(fields) if signature_in_check else {k: v for k, v in fields.items() if k != "signature"}
    dcs = "\n".join(f"{k}={check[k]}" for k in sorted(check))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _request(init_data: str = "", query: dict | None = None, headers: dict | None = None):
    h = dict(headers or {})
    if init_data:
        h["Authorization"] = f"tma {init_data}"
    return SimpleNamespace(headers=h, query_params=dict(query or {}))


class ApiAuthTests(unittest.TestCase):
    def setUp(self):
        self.ns = _load()
        patcher = mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "ADMIN_STATS_KEY": "s3cret-key"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_telegram_id_param_alone_is_rejected(self):
        with mock.patch.dict(os.environ, {"SKIP_TMA_AUTH": "true"}):
            with self.assertRaises(HTTPException) as ctx:
                self.ns["_auth_telegram_id"](_request(query={"telegram_id": "42"}), "42")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_valid_init_data_with_and_without_signature(self):
        for with_sig, sig_in_check in ((False, False), (True, True), (True, False)):
            data = _signed_init_data(77, with_signature=with_sig, signature_in_check=sig_in_check)
            self.assertEqual(self.ns["_auth_telegram_id"](_request(data), None), "77")

    def test_tampered_init_data_is_rejected(self):
        data = _signed_init_data(77, with_signature=True, signature_in_check=True)
        forged = data.replace("%22id%22%3A+77", "%22id%22%3A+78")
        self.assertNotEqual(forged, data)
        with self.assertRaises(HTTPException):
            self.ns["_auth_telegram_id"](_request(forged), "78")

    def test_admin_requires_key_or_admin_init_data(self):
        is_admin = self.ns["_is_admin_request"]
        self.assertFalse(is_admin(_request(query={"admin_id": str(ADMIN)})))
        self.assertFalse(is_admin(_request(headers={"X-Admin-Key": "wrong"})))
        self.assertTrue(is_admin(_request(headers={"X-Admin-Key": "s3cret-key"})))
        self.assertTrue(is_admin(_request(_signed_init_data(ADMIN, with_signature=False, signature_in_check=False))))
        self.assertFalse(is_admin(_request(_signed_init_data(5, with_signature=False, signature_in_check=False))))

    def test_empty_admin_key_never_matches(self):
        with mock.patch.dict(os.environ, {"ADMIN_STATS_KEY": ""}):
            self.assertFalse(self.ns["_is_admin_request"](_request(headers={"X-Admin-Key": ""})))

    def test_bypass_is_gone_from_code(self):
        self.assertNotIn("SKIP_TMA_AUTH", (ROOT / "main.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

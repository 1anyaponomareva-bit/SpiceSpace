#!/usr/bin/env python3
"""Reset last_*_sent_date to NULL for a user (Supabase REST)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for env_path in (ROOT / ".env", ROOT.parent / ".env"):
    if not env_path.exists():
        continue
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)

import db as db_store

USER_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 8412438788

if not db_store.init_db():
    print("NO_SUPABASE: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
    sys.exit(1)

body = {
    "last_morning_sent_date": None,
    "last_evening_sent_date": None,
    "last_daily_sent_date": None,
}
patched = db_store._request(
    "PATCH",
    f"user_profiles?user_id=eq.{USER_ID}",
    json=body,
    headers={**db_store._headers(), "Prefer": "return=representation"},
)
print("PATCH result:", patched)
rows = db_store._request(
    "GET",
    f"user_profiles?user_id=eq.{USER_ID}"
    "&select=user_id,last_morning_sent_date,last_evening_sent_date,last_daily_sent_date",
)
print("After:", rows)

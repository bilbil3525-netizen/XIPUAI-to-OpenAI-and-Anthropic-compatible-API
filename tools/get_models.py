"""Fetch the live model list from the school platform and print it.

Handy when the school adds/removes models - just re-run and refresh
adapter.py's AVAILABLE_MODELS (or use --json to dump them).

Usage:
    python tools/get_models.py [--json]
"""

import argparse
import json
import os
import sys

import httpx
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

BASE = "https://xipuai.xjtlu.edu.cn/jmapi/api/chat"


def headers() -> dict:
    token = os.getenv("JM_TOKEN")
    if not token:
        sys.exit("JM_TOKEN missing - run auth_v4.py first")
    h = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://xipuai.xjtlu.edu.cn",
        "referer": "https://xipuai.xjtlu.edu.cn/v3/chat",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/155.0.0.0 Safari/537.36",
        "jm-token": token,
    }
    if os.getenv("BROWSERID"):
        h["browserid"] = os.environ["BROWSERID"]
    return h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit a JSON array of model ids")
    args = ap.parse_args()

    with httpx.Client(timeout=30, trust_env=False) as c:
        r = c.get(f"{BASE}/config?sf_request_type=ajax", headers=headers())
        r.raise_for_status()
        data = r.json().get("data", {})
    models = data.get("models", [])

    values = [m.get("value") or m.get("id") for m in models]

    if args.json:
        print(json.dumps(values, ensure_ascii=False))
    else:
        print(f"{len(values)} models available:\n")
        for m in models:
            print(f"  {(m.get('value') or m.get('id')):34s} {m.get('label', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

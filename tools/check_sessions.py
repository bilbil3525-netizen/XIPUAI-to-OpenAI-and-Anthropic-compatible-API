"""Inspect (and optionally clean) the chat sessions on your account.

The relay creates one session per request and deletes it right after, but a
hard kill can leave orphans behind. The adapter cleans them on startup; this
tool lets you check by hand.

The 2026 platform caps accounts at 50 sessions, so orphans eventually matter.

Usage:
    python tools/check_sessions.py            # list
    python tools/check_sessions.py --clean    # delete relay-created orphans
"""

import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

BASE = "https://xipuai.xjtlu.edu.cn/jmapi/api/chat"

# Session names the adapter generates. Anything else is treated as the user's own.
RELAY_NAMES = {"Notes", "New Chat", "新对话", "问题", "求助",
               "Discussion", "Research", "Summary", "Draft", "Review"}


def headers() -> dict:
    token = os.getenv("JM_TOKEN")
    if not token:
        sys.exit("JM_TOKEN missing - run auth_v4.py first")
    h = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://xipuai.xjtlu.edu.cn",
        "referer": "https://xipuai.xjtlu.edu.cn/v3/chat",
        "jm-token": token,
    }
    if os.getenv("BROWSERID"):
        h["browserid"] = os.environ["BROWSERID"]
    return h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true",
                    help="delete sessions that look relay-generated")
    args = ap.parse_args()

    with httpx.Client(timeout=30, trust_env=False) as c:
        r = c.get(f"{BASE}/session?sf_request_type=ajax", headers=headers())
        r.raise_for_status()
        sessions = r.json().get("data", []) or []

        print(f"{len(sessions)} session(s) on this account:\n")
        orphans = []
        for s in sessions:
            name = str(s.get("name", ""))
            sid = s.get("id")
            model = str(s.get("model", ""))
            tag = ""
            if name in RELAY_NAMES:
                tag = "  <- relay"
                orphans.append(sid)
            print(f"  id={sid:<9} {model:<22} {name[:52]}{tag}")

        if not args.clean:
            if orphans:
                print(f"\n{len(orphans)} look relay-generated. "
                      f"Re-run with --clean to delete them.")
            return 0

        if not orphans:
            print("\nNothing to clean.")
            return 0

        rr = c.post(f"{BASE}/delSession?sf_request_type=ajax",
                    headers=headers(), json={"ids": [int(i) for i in orphans]})
        print(f"\ndeleted {len(orphans)} session(s): {rr.status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

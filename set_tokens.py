#!/usr/bin/env python3
"""Manually write XIPUAI tokens into .env.

Fallback for when `auth.py` (Selenium automation) fails — e.g. MFA, captcha, or a
changed login page. Grab the two values from Chrome DevTools instead:

  1. Open https://xipuai.xjtlu.edu.cn/ and log in, then go to /v3/chat
  2. DevTools (F12) -> Network -> filter "jmapi"
  3. Click any request -> Headers -> Request Headers -> copy:
        jm-token:        <value>
        sdp-app-session: <value>
  4. Run this script and paste them.

Usage:  ./.venv/bin/python set_tokens.py
"""

import os
import sys

from dotenv import load_dotenv, set_key

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def main() -> int:
    if not os.path.exists(ENV_FILE):
        open(ENV_FILE, "a").close()

    load_dotenv(ENV_FILE)

    print("=== Manually set XIPUAI tokens ===")
    print(f"(.env: {ENV_FILE})\n")

    jm_token = os.getenv("JM_TOKEN") or ""
    sdp_session = os.getenv("SDP_SESSION") or ""

    hint_jm = f" [{jm_token[:12]}...]" if jm_token else ""
    hint_sdp = f" [{sdp_session[:12]}...]" if sdp_session else ""

    new_jm = input(f"jm-token{hint_jm}: ").strip() or jm_token
    new_sdp = input(f"sdp-app-session{hint_sdp}: ").strip() or sdp_session

    if not new_jm or not new_sdp:
        print("❌ Both values are required.")
        return 1

    set_key(ENV_FILE, "JM_TOKEN", new_jm)
    set_key(ENV_FILE, "SDP_SESSION", new_sdp)
    print("\n✅ Tokens saved to .env. Start the service with: bash ../start-real.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())

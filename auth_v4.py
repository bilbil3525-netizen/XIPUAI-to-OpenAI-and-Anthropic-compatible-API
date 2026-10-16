"""XJTLU 君谋AI token fetcher v4 (final) - matches the redesigned platform.

What changed on the school side (verified against live traffic):

    OLD                                          NEW
    https://jmapi.xjtlu.edu.cn/api/chat/...  ->  https://xipuai.xjtlu.edu.cn/jmapi/api/chat/...
    headers: jm-token + sdp-app-session      ->  header: jm-token only
    login page: #username_show/#password_show -> /v3/login -> XJTLU SSO -> uim.xjtlu.edu.cn

This script performs the SSO login headlessly and captures jm-token, writing it
to .env as JM_TOKEN (plus BROWSERID / LANGUAGE when present).
"""

import os
import sys
import time
from urllib.parse import urlparse

from dotenv import load_dotenv, set_key
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from seleniumwire import webdriver

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, ".env")

API_HOST_MARK = "/jmapi/api/chat"


def log(m):
    print(m, flush=True)


def click_text(d, needle, tags=("button", "a", "div", "span")):
    for tag in tags:
        for el in d.find_elements(By.TAG_NAME, tag):
            try:
                t = (el.text or "").strip()
            except Exception:
                continue
            if needle.lower() in t.lower() and len(t) < 40:
                d.execute_script("arguments[0].click();", el)
                return t
    return None


def main():
    load_dotenv(ENV_FILE)
    user = os.getenv("XJTLU_USERNAME")
    pw = os.getenv("XJTLU_PASSWORD")
    if not user or not pw:
        log("ERROR: XJTLU_USERNAME / XJTLU_PASSWORD missing from .env")
        return 1

    o = webdriver.EdgeOptions()
    o.use_chromium = True
    for a in ["--headless=new", "--disable-gpu", "--no-sandbox", "--window-size=1920,1080",
              "--proxy-server=direct://", "--disable-proxy-discovery"]:
        o.add_argument(a)
    d = webdriver.Edge(seleniumwire_options={"disable_encoding": True}, options=o)

    got = {}
    api_base = {}

    def intercept(req):
        low = {k.lower(): v for k, v in req.headers.items()}
        if "jm-token" in low and "jm-token" not in got:
            got["jm-token"] = low["jm-token"]
        for k in ("browserid", "language"):
            if k in low and k not in got:
                got[k] = low[k]
        if API_HOST_MARK in req.url:
            u = urlparse(req.url)
            api_base[u.scheme + "://" + u.netloc + API_HOST_MARK] = True

    d.request_interceptor = intercept

    try:
        log("1. open https://xipuai.xjtlu.edu.cn/v3/chat")
        d.get("https://xipuai.xjtlu.edu.cn/v3/chat")
        time.sleep(5)

        if "login" in d.current_url:
            log("2. login page: tick agreement + click XJTLU SSO Login")
            for cb in d.find_elements(By.CSS_SELECTOR, "input[type=checkbox]"):
                try:
                    if not cb.is_selected():
                        d.execute_script("arguments[0].click();", cb)
                except Exception:
                    pass
            time.sleep(1)
            click_text(d, "SSO")
            time.sleep(8)

        if d.find_elements(By.CSS_SELECTOR, "input[type=password]"):
            log("3. SSO form: fill credentials")
            WebDriverWait(d, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type=password]")))
            time.sleep(2)
            d.find_element(By.CSS_SELECTOR, "input[type=text]").send_keys(user)
            d.find_element(By.CSS_SELECTOR, "input[type=password]").send_keys(pw)
            time.sleep(1)
            click_text(d, "Sign in", tags=("button",))
            log("   submitted")

        log("4. waiting for jm-token (up to 90s)")
        for _ in range(90):
            if "jm-token" in got:
                time.sleep(5)
                break
            time.sleep(1)

        if "jm-token" not in got:
            log("FAILED: no jm-token captured")
            d.save_screenshot(os.path.join(HERE, "v4_fail.png"))
            return 1

        set_key(ENV_FILE, "JM_TOKEN", got["jm-token"])
        for k in ("browserid", "language"):
            if k in got:
                set_key(ENV_FILE, k.upper(), got[k])

        log("SUCCESS - jm-token captured and saved")
        log("  api base seen: " + (", ".join(api_base) if api_base else "(none)"))
        log(f"  browserid: {'yes' if 'browserid' in got else 'no'}")
        log(f"  final url: {d.current_url}")
        return 0
    finally:
        try:
            d.quit()
        except Exception:
            pass


if __name__ == "__main__":
    print("=" * 58)
    print(" XJTLU 君谋AI token fetcher v4")
    print("=" * 58)
    sys.exit(main())

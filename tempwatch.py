#!/usr/bin/env python3
"""Morgonnotis (07:30) med temperaturer från Ecowitt och Adax till Telegram.

Endast standardbibliotek. Konfiguration via miljövariabler (se .env.example).
Körning:
  python offerberg_temp.py --once      # skicka direkt en gång
  python offerberg_temp.py --dry-run   # skriv ut meddelandet, skicka inte
  python offerberg_temp.py             # loop: skickar varje dag kl 07:30
"""
import argparse
import datetime as dt
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("TZ", "Europe/Stockholm"))
SEND_AT = dt.time(7, 30)
TITLE = "Offerberg temp"


def http_json(url, data=None, headers=None, timeout=20):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code} {detail}") from None


def clean(v):
    """Tar bort blanksteg och citattecken (docker env_file behåller dem)."""
    return v.strip().strip("\"'")


def ecowitt_temps(env):
    """Returnerar (utomhus, inomhus) i °C. Ecowitt API v3 real_time."""
    qs = urllib.parse.urlencode({
        "application_key": env["ECOWITT_APPLICATION_KEY"],
        "api_key": env["ECOWITT_API_KEY"],
        "mac": env["ECOWITT_MAC"],
        "call_back": "outdoor,indoor",
        "temp_unitid": 1,  # 1 = °C
    })
    j = http_json(f"https://api.ecowitt.net/api/v3/device/real_time?{qs}")
    if j.get("code") != 0:
        raise RuntimeError(f"Ecowitt: {j.get('msg')}")
    d = j["data"]
    return (float(d["outdoor"]["temperature"]["value"]),
            float(d["indoor"]["temperature"]["value"]))


def adax_rooms_heating(env):
    """Returnerar [(rumsnamn, °C)] för rum där värme är på."""
    tok = http_json(
        "https://api-1.adax.no/client-api/auth/token",
        data={"grant_type": "password",
              "username": clean(env["ADAX_CLIENT_ID"]),
              "password": clean(env["ADAX_CLIENT_SECRET"])},
    )["access_token"]
    j = http_json("https://api-1.adax.no/client-api/rest/v1/content/",
                  headers={"Authorization": f"Bearer {tok}"})
    return heating_rooms(j)


def heating_rooms(j):
    """Rum med värme på. Adax anger temperaturer som heltal gånger 100."""
    return [(r["name"], r["temperature"] / 100)
            for r in j.get("rooms", [])
            if r.get("heatingEnabled") and "temperature" in r]


def fmt(v):
    return f"{v:.1f}".replace(".", ",") + " °C"


def build_message(outdoor, indoor, rooms):
    lines = [TITLE, f"Utomhus: {fmt(outdoor)}", f"Inomhus: {fmt(indoor)}",
             "Dessa rum har värme på:"]
    lines += [f"  {n}: {fmt(t)}" for n, t in rooms] or ["  (inga)"]
    return "\n".join(lines)


def telegram_token(env):
    """Samma namn som baxlio (TELEGRAM_TOKEN); TELEGRAM_BOT_TOKEN som reserv."""
    return clean(env.get("TELEGRAM_TOKEN") or env.get("TELEGRAM_BOT_TOKEN") or "")


def telegram_html(text):
    """Första raden (rubriken) i fetstil, resten som vanlig text."""
    head, _, rest = text.partition("\n")
    out = f"<b>{html.escape(head)}</b>"
    return out + ("\n" + html.escape(rest) if rest else "")


def telegram_send(env, text):
    token, chat = telegram_token(env), clean(env.get("TELEGRAM_CHAT_ID", ""))
    if not token or not chat:
        raise RuntimeError("Telegram: TELEGRAM_TOKEN eller TELEGRAM_CHAT_ID saknas")
    body = json.dumps({"chat_id": chat, "text": telegram_html(text),
                       "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", body,
        {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram HTTP {e.code} "
                           f"{e.read().decode(errors='replace')[:200]}") from None
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram: {resp}")


def run_once(env, dry=False):
    # Delfel ska inte stoppa notisen: visa vad som saknas.
    parts, errors = {}, []
    try:
        parts["eco"] = ecowitt_temps(env)
    except Exception as e:
        errors.append(f"Ecowitt otillgänglig ({e})")
    try:
        parts["adax"] = adax_rooms_heating(env)
    except Exception as e:
        errors.append(f"Adax otillgänglig ({e})")
    if "eco" in parts:
        msg = build_message(*parts["eco"], parts.get("adax", []))
    else:
        msg = TITLE
    if "adax" not in parts and "eco" in parts:
        msg = msg.replace("Dessa rum har värme på:\n  (inga)",
                          "Dessa rum har värme på: okänt")
    if errors:
        msg += "\n⚠ " + "; ".join(errors)
    print(msg)
    if not dry:
        telegram_send(env, msg)


def seconds_until_next(now=None):
    now = now or dt.datetime.now(TZ)
    nxt = now.replace(hour=SEND_AT.hour, minute=SEND_AT.minute,
                      second=0, microsecond=0)
    if nxt <= now:
        nxt = (nxt + dt.timedelta(days=1)).replace(
            hour=SEND_AT.hour, minute=SEND_AT.minute)
    return (nxt - now).total_seconds()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    env = os.environ
    if a.once or a.dry_run:
        run_once(env, dry=a.dry_run)
        return
    while True:
        time.sleep(seconds_until_next())
        try:
            run_once(env)
        except Exception as e:  # håll loopen vid liv
            print(f"Fel: {e}", file=sys.stderr)
        time.sleep(60)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Morgonnotis (07:30) med temperaturer från Ecowitt och Adax till Telegram.

Endast standardbibliotek. Konfiguration via miljövariabler (se .env.example).
Körning:
  python tempwatch.py --once      # skicka direkt en gång
  python tempwatch.py --dry-run   # skriv ut meddelandet, skicka inte
  python tempwatch.py             # loop: skickar varje dag kl 07:30
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


ADAX = "https://api-1.adax.no/client-api"


def adax_token(env):
    return http_json(
        f"{ADAX}/auth/token",
        data={"grant_type": "password",
              "username": clean(env["ADAX_CLIENT_ID"]),
              "password": clean(env["ADAX_CLIENT_SECRET"])},
    )["access_token"]


def adax_content(token):
    return http_json(f"{ADAX}/rest/v1/content/",
                     headers={"Authorization": f"Bearer {token}"})


def adax_points(token, room_id):
    j = http_json(f"{ADAX}/rest/v1/energy_log/{room_id}",
                  headers={"Authorization": f"Bearer {token}"})
    return j.get("points", [])


def energy_since(points, start_ms, now_ms, snaps=()):
    """Wh sedan start_ms ur Adax dygnspunkter (04:00-04:00).

    Ett dygn som korsar start_ms räknas som (värde nu) - (värde vid start).
    Värdet vid start tas från en sparad ögonblicksbild (exakt) annars från
    en jämn fördelning över dygnet (uppskattning).
    Returnerar (Wh, uppskattat, nuvarande dygnspunkt eller None)."""
    seen, total, estimated, current = set(), 0.0, False, None
    for p in points:
        f, t, wh = int(p["fromTime"]), int(p["toTime"]), float(p["energyWh"])
        if (f, t) in seen:
            continue
        seen.add((f, t))
        if f <= now_ms < t:
            current = {"from": f, "wh": wh}
        if t <= start_ms or f >= now_ms:
            continue
        if f >= start_ms:
            total += wh
            continue
        # Dygnet korsar starttiden.
        near = [x for x in snaps if x["from"] == f
                and abs(x["at"] - start_ms) <= 90 * 60 * 1000]
        if near:
            at_start = min(near, key=lambda x: abs(x["at"] - start_ms))["wh"]
        else:
            elapsed = min(t, now_ms) - f
            at_start = wh * (start_ms - f) / elapsed if elapsed > 0 else 0.0
            estimated = True
        total += max(wh - at_start, 0.0)
    return total, estimated, current


def window_start(now=None):
    """Igår kl 07:30 lokal tid (ms)."""
    now = now or dt.datetime.now(TZ)
    y = (now - dt.timedelta(days=1)).replace(
        hour=SEND_AT.hour, minute=SEND_AT.minute, second=0, microsecond=0)
    return int(y.timestamp() * 1000)


def state_path(env):
    return clean(env.get("TEMPWATCH_STATE") or "/state/snapshots.json")


def load_state(env):
    try:
        with open(state_path(env)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(env, state):
    path = state_path(env)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def adax_energy(env, token, room_ids, start_ms, now_ms, state):
    """(Wh, uppskattat, nya ögonblicksbilder {rum: [...]})."""
    total, est, new = 0.0, False, {}
    cutoff = now_ms - 4 * 86400 * 1000
    for rid in room_ids:
        key = str(rid)
        snaps = [x for x in state.get(key, []) if x["at"] > cutoff]
        wh, e, cur = energy_since(adax_points(token, rid), start_ms, now_ms, snaps)
        total += wh
        est = est or e
        if cur:
            snaps.append({"at": int(now_ms), **cur})
        new[key] = snaps
    return total, est, new


def spot_average(area, start_ms, now_ms):
    """Medelspotpris (kr/kWh, exkl. moms) för elområdet över perioden.

    Källa: elprisetjustnu.se (öppet API, ingen nyckel). Obevakat medelvärde
    över alla timmar/kvartar i perioden; saknade dagar hoppas över."""
    start = dt.datetime.fromtimestamp(start_ms / 1000, TZ)
    end = dt.datetime.fromtimestamp(now_ms / 1000, TZ)
    vals, day = [], start.date()
    while day <= end.date():
        url = (f"https://www.elprisetjustnu.se/api/v1/prices/"
               f"{day.year}/{day:%m-%d}_{area}.json")
        try:
            rows = http_json(url)
        except RuntimeError as e:
            if "HTTP 404" not in str(e):
                raise
            rows = []
        for r in rows:
            t = dt.datetime.fromisoformat(r["time_start"])
            if start <= t < end:
                vals.append(float(r["SEK_per_kWh"]))
        day += dt.timedelta(days=1)
    return sum(vals) / len(vals) if vals else None


def price_per_kwh(env, start_ms, now_ms):
    """(pris kr/kWh inkl. moms | None, anteckning).

    Med ELECTRICITY_AREA (t.ex. SE4): medelspot + ELECTRICITY_ADDON_SEK_PER_KWH
    (nät, skatt, påslag; exkl. moms) + moms (ELECTRICITY_VAT, standard 0,25).
    Annars fast ELECTRICITY_PRICE_SEK_PER_KWH."""
    area = clean(env.get("ELECTRICITY_AREA") or "").upper()
    if area and start_ms:
        avg = spot_average(area, start_ms, now_ms)
        if avg is None:
            raise RuntimeError(f"inga spotpriser hittades för {area} i perioden")
        if avg is not None:
            addon = parse_price(env.get("ELECTRICITY_ADDON_SEK_PER_KWH")) or 0.0
            vat = parse_price(env.get("ELECTRICITY_VAT"))
            vat = 0.25 if vat is None else vat
            price = (avg + addon) * (1 + vat)
            return price, f"{fmt_num(avg, 2)} kr/kWh spot {area}, exkl. moms"
    return parse_price(env.get("ELECTRICITY_PRICE_SEK_PER_KWH")), None


def parse_price(v):
    """Pris per kWh i kr, t.ex. '1,25' eller '1.25'. None om inte angivet."""
    v = clean(v or "").replace(",", ".")
    return float(v) if v else None


def heating_rooms(j):
    """Rum med värme på. Adax anger temperaturer som heltal gånger 100."""
    return [(r["name"], r["temperature"] / 100)
            for r in j.get("rooms", [])
            if r.get("heatingEnabled") and "temperature" in r]


def fmt(v):
    return f"{v:.1f}".replace(".", ",") + " °C"


def fmt_num(v, dec):
    return f"{v:.{dec}f}".replace(".", ",")


def since_label(start_ms, now=None):
    """'igår 04:00' / 'idag 04:00' i lokal tid."""
    now = now or dt.datetime.now(TZ)
    st = dt.datetime.fromtimestamp(start_ms / 1000, TZ)
    day = "idag" if st.date() == now.date() else "igår"
    return f"{day} {st:%H:%M}"


def energy_line(wh, price, start_ms=None, now=None, note=None, estimated=False):
    kwh = wh / 1000
    since = f" sedan {since_label(start_ms, now)}" if start_ms else ""
    line = f"Energiförbrukning{since}: {fmt_num(kwh, 1)} kWh"
    if price is not None:
        line += f" (≈ {fmt_num(kwh * price, 2)} kr)"
    if estimated:
        line += " – uppskattat"
    if note and price is not None:
        line += f"\n  Snittspot: {note}"
    if price is None:
        line += " (elpris ej inställt)"
    return line


def build_message(outdoor, indoor, rooms, energy=None):
    lines = [TITLE, f"Utomhus: {fmt(outdoor)}", f"Inomhus: {fmt(indoor)}",
             "Dessa rum har värme på:"]
    lines += [f"  {n}: {fmt(t)}" for n, t in rooms] or ["  (inga)"]
    if energy:
        lines.append(energy)
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
    eco = rooms = energy = None
    errors = []
    try:
        eco = ecowitt_temps(env)
    except Exception as e:
        errors.append(f"Ecowitt otillgänglig ({e})")
    try:
        token = adax_token(env)
        content = adax_content(token)
        rooms = heating_rooms(content)
        try:
            now_ms = time.time() * 1000
            start = window_start()
            state = load_state(env)
            wh, est, new = adax_energy(
                env, token, [r["id"] for r in content.get("rooms", [])],
                start, now_ms, state)
            try:
                price, note = price_per_kwh(env, start, now_ms)
            except Exception as e:
                errors.append(f"Spotpris otillgängligt ({e})")
                price, note = parse_price(
                    env.get("ELECTRICITY_PRICE_SEK_PER_KWH")), None
            energy = energy_line(wh, price, start, note=note, estimated=est)
            if not dry:
                try:
                    save_state(env, new)
                except OSError as e:
                    errors.append(f"Kan inte spara tillstånd ({e})")
        except Exception as e:
            errors.append(f"Energi otillgänglig ({e})")
    except Exception as e:
        errors.append(f"Adax otillgänglig ({e})")
    if eco:
        msg = build_message(*eco, rooms or [], energy)
        if rooms is None:
            msg = msg.replace("Dessa rum har värme på:\n  (inga)",
                              "Dessa rum har värme på: okänt")
    else:
        msg = "\n".join([TITLE] + ([energy] if energy else []))
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


def debug_energy(env):
    """Skriver ut Adax rå energy_log (utan hemligheter) för felsökning."""
    token = adax_token(env)
    now_ms = time.time() * 1000
    start = window_start()
    state = load_state(env)
    for r in adax_content(token).get("rooms", []):
        pts = adax_points(token, r["id"])
        res = energy_since(pts, start, now_ms, state.get(str(r["id"]), []))
        print(f"{r['name']} (id {r['id']}): punkter={len(pts)} "
              f"sedan_start_Wh={res[0]:.0f} uppskattat={res[1]} nu={res[2]}")
        for p in (pts[:2] + pts[-2:]) if pts else []:
            print("   ", p)
    print(f"start_ms={start} nu_ms={int(now_ms)} tillstånd={state_path(env)}")


def debug_price(env):
    """Visar vilka prisinställningar som lästs och vad spot-API:et ger."""
    keys = ("ELECTRICITY_AREA", "ELECTRICITY_ADDON_SEK_PER_KWH",
            "ELECTRICITY_VAT", "ELECTRICITY_PRICE_SEK_PER_KWH")
    for k in keys:
        print(f"{k} = {clean(env.get(k) or '')!r}")
    area = clean(env.get("ELECTRICITY_AREA") or "").upper()
    if not area:
        print("ELECTRICITY_AREA saknas -> inget spotpris används")
        return
    now = dt.datetime.now(TZ)
    url = (f"https://www.elprisetjustnu.se/api/v1/prices/"
           f"{now.year}/{now:%m-%d}_{area}.json")
    print("URL:", url)
    rows = http_json(url)
    print(f"antal rader: {len(rows)}")
    for r in rows[:2] + rows[-1:]:
        print("   ", r)
    start = int(now.replace(hour=4, minute=0, second=0, microsecond=0)
                .timestamp() * 1000)
    print("pris/kWh inkl. moms (idag från 04:00):",
          price_per_kwh(env, start, time.time() * 1000))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug-price", action="store_true")
    ap.add_argument("--debug-energy", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    env = os.environ
    if a.debug_price:
        debug_price(env)
        return
    if a.debug_energy:
        debug_energy(env)
        return
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

#!/usr/bin/env python3
"""Morgonnotis (07:30) med temperaturer, energi och elkostnad till Telegram.

Datakällor: Ecowitt (ute/inne), Adax (rum, element) och elprisetjustnu.se.
Endast standardbibliotek. Konfiguration via miljövariabler (se .env.example).
Körning:
  python tempwatch.py --once      # skicka direkt en gång
  python tempwatch.py --dry-run   # skriv ut meddelandet, skicka inte
  python tempwatch.py             # loop: skickar kl 07:30 och loggar element var 15:e minut
Felsökning: --debug-price, --debug-devices, --debug-energy
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
USER_AGENT = "tempwatch/1.0 (+https://github.com/svenbox/tempwatch)"


def http_json(url, data=None, headers=None, timeout=20, retries=2):
    """JSON-anrop. Försöker om vid serverfel (5xx) och nätverksfel."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code >= 500 and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {e.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"nätverksfel: {e}") from None


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
    """(Wh, uppskattat, nya ögonblicksbilder {rum: [...]}) ur Adax dygnspunkter."""
    total, est, new = 0.0, False, {}
    cutoff = now_ms - 4 * 86400 * 1000
    for rid in room_ids:
        key = str(rid)
        snaps = [x for x in state.get(key, []) if x["at"] > cutoff]
        pts = adax_points(token, rid)
        wh, e, cur = energy_since(pts, start_ms, now_ms, snaps)
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

    Med ELECTRICITY_AREA (t.ex. SE2): medelspot + ELECTRICITY_ADDON_SEK_PER_KWH
    (nät, skatt, påslag; exkl. moms) + moms (ELECTRICITY_VAT, standard 0,25).
    Annars fast ELECTRICITY_PRICE_SEK_PER_KWH."""
    area = clean(env.get("ELECTRICITY_AREA") or "").upper()
    if area and start_ms:
        avg = spot_average(area, start_ms, now_ms)
        if avg is None:
            raise RuntimeError(f"inga spotpriser hittades för {area} i perioden")
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
    return [(r["name"], r["temperature"] / 100,
             r["targetTemperature"] / 100 if "targetTemperature" in r else None)
            for r in j.get("rooms", [])
            if r.get("heatingEnabled") and "temperature" in r]


def fmt(v):
    return f"{v:.1f}".replace(".", ",") + " °C"


def fmt_target(v):
    """Måltemperatur utan onödig decimal: 10 °C, 10,5 °C."""
    return (f"{v:.0f}" if abs(v - round(v)) < 0.05
            else f"{v:.1f}".replace(".", ",")) + " °C"


def room_line(name, temp, target=None):
    line = f"  {name}: {fmt(temp)}"
    return line + (f" (mål {fmt_target(target)})" if target is not None else "")


def fmt_num(v, dec):
    return f"{v:.{dec}f}".replace(".", ",")


def since_label(start_ms, now=None):
    """'igår 04:00' / 'idag 04:00' i lokal tid."""
    now = now or dt.datetime.now(TZ)
    st = dt.datetime.fromtimestamp(start_ms / 1000, TZ)
    day = "idag" if st.date() == now.date() else "igår"
    return f"{day} {st:%H:%M}"


def energy_line(wh, price, start_ms=None, now=None, estimated=False):
    kwh = wh / 1000
    since = f" sedan {since_label(start_ms, now)}" if start_ms else ""
    line = f"Energiförbrukning{since}: {fmt_num(kwh, 1)} kWh"
    if price is not None:
        line += f" (≈ {fmt_num(kwh * price, 2)} kr)"
    if estimated:
        line += " – uppskattat"
    if price is None:
        line += " (elpris ej inställt)"
    return line


def build_message(outdoor, indoor, rooms, energy=None):
    lines = [TITLE, f"Utomhus: {fmt(outdoor)}", f"Inomhus (köket): {fmt(indoor)}",
             "Dessa rum har värme på:"]
    lines += [room_line(*r) for r in rooms] or ["  (inga)"]
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
    """Skickar via Bot API med omförsök vid 5xx/nätverksfel.

    Ett omförsök efter ett förlorat svar kan i sällsynta fall ge dubbletter;
    det är bättre än att dagens notis uteblir."""
    token, chat = telegram_token(env), clean(env.get("TELEGRAM_CHAT_ID", ""))
    if not token or not chat:
        raise RuntimeError("Telegram: TELEGRAM_TOKEN eller TELEGRAM_CHAT_ID saknas")
    resp = http_json(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat, "text": telegram_html(text),
              "parse_mode": "HTML", "disable_web_page_preview": "true"},
        retries=3)
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram: {resp}")


def run_once(env, dry=False):
    # Delfel ska inte stoppa notisen: visa vad som saknas.
    eco = rooms = energy = content = token = None
    errors = []
    now_ms = time.time() * 1000
    start = window_start()
    try:
        rows = load_samples(env, now_ms)
    except Exception as e:
        rows = []
        errors.append(f"Provlogg otillgänglig ({e})")
    try:
        eco = ecowitt_temps(env)
    except Exception as e:
        errors.append(f"Ecowitt otillgänglig ({e})")
    try:
        token = adax_token(env)
        content = adax_content(token)
        rooms = heating_rooms(content)
    except Exception as e:
        errors.append(f"Adax otillgänglig ({e})")
    try:
        # Primärt egna prover (täcker exakt perioden); annars Adax dygnspunkter.
        wh, est = own_energy(rows, start, now_ms), False
        if wh is None:
            if content is None:
                raise RuntimeError("för få egna prover och Adax otillgängligt")
            wh, est, new = adax_energy(
                env, token, [r["id"] for r in content.get("rooms", [])],
                start, now_ms, load_state(env))
            if not dry:
                try:
                    save_state(env, new)
                except OSError as e:
                    errors.append(f"Kan inte spara tillstånd ({e})")
        try:
            price, _ = price_per_kwh(env, start, now_ms)
        except Exception as e:
            errors.append(f"Spotpris otillgängligt ({e})")
            price = parse_price(env.get("ELECTRICITY_PRICE_SEK_PER_KWH"))
        energy = energy_line(wh, price, start, estimated=est)
    except Exception as e:
        errors.append(f"Energi otillgänglig ({e})")
    if eco:
        msg = build_message(*eco, rooms or [], energy)
        if rooms is None:
            msg = msg.replace("Dessa rum har värme på:\n  (inga)",
                              "Dessa rum har värme på: okänt")
    else:
        msg = "\n".join([TITLE] + ([energy] if energy else []))
    try:
        f = parse_price(env.get("ANOMALY_FACTOR")) or 1.5
        m = parse_price(env.get("ANOMALY_MIN_EXTRA_WH")) or 1000.0
        mb = parse_price(env.get("ANOMALY_MIN_BASELINE_WH")) or 500.0
        anoms, hist = find_anomalies(rows, now_ms, f, m, mb)
        msg += "\n" + "\n".join(anomaly_lines(anoms, hist, rows))
    except Exception as e:
        errors.append(f"Elementkontroll misslyckades ({e})")
    if errors:
        msg += "\n⚠ " + "; ".join(errors)
    print(msg)
    if not dry:
        telegram_send(env, msg)


SAMPLE_EVERY = 15 * 60


def samples_path(env):
    return os.path.join(os.path.dirname(state_path(env)), "devices.jsonl")


def sample_rows(content, now_ms):
    """En rad per element: tid, enhet, rum, Wh och Adax energyTime."""
    return [{"t": int(now_ms), "id": d.get("id"), "room": d.get("roomId"),
             "name": d.get("name"), "wh": d.get("energyWh"),
             "et": d.get("energyTime")}
            for d in content.get("devices", [])]


def sample_devices(env):
    """Loggar Adax timenergi per element (underlag för att avgöra om den går att lita på)."""
    token = adax_token(env)
    j = http_json(f"{ADAX}/rest/v1/content/?withEnergy=1",
                  headers={"Authorization": f"Bearer {token}"})
    rows = sample_rows(j, time.time() * 1000)
    path = samples_path(env)
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    try:
        if os.path.getsize(path) > 1_000_000:
            prune_samples(env, time.time() * 1000)
    except OSError:
        pass


DAY_MS = 24 * 3600 * 1000
MIN_SAMPLES_PER_DAY = 48  # av max 96 (var 15:e minut); färre = för tunn data
MIN_HISTORY_DAYS = 3


def load_samples(env, now_ms, days=9):
    """Läser devices.jsonl; hoppar över trasiga rader och dubbletter (id, et)."""
    rows, seen = [], set()
    try:
        with open(samples_path(env)) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    key = (r["id"], r.get("et"))
                    if r["t"] < now_ms - days * DAY_MS or key in seen:
                        continue
                    r["wh"] = float(r["wh"])
                    seen.add(key)
                    rows.append(r)
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    return rows


def own_energy(rows, start_ms, now_ms, room=None, min_cover=0.5):
    """Wh i [start, nu) ur egna prover, eller None om täckningen är för tunn.

    Varje prov är elementets energi senaste timmen, så medelvärdet av proven
    gånger antalet timmar ger energin över perioden. Kräver prover nära både
    start och slut och minst hälften av de förväntade provtagningarna."""
    by = {}
    for r in rows:
        if start_ms <= r["t"] < now_ms and (room is None or r.get("room") == room):
            by.setdefault(r["id"], []).append((r["t"], r["wh"]))
    hours = (now_ms - start_ms) / 3_600_000
    if not by or hours <= 0:
        return None
    ts = [t for v in by.values() for t, _ in v]
    if min(ts) - start_ms > 2 * 3_600_000 or now_ms - max(ts) > 2 * 3_600_000:
        return None
    expected = (now_ms - start_ms) / 1000 / SAMPLE_EVERY
    if max(len(v) for v in by.values()) < min_cover * expected:
        return None
    return sum(sum(w for _, w in v) / len(v) for v in by.values()) * hours


def prune_samples(env, now_ms, keep_days=14):
    """Tar bort prover äldre än keep_days (atomiskt)."""
    path = samples_path(env)
    cutoff = now_ms - keep_days * DAY_MS
    kept = []
    with open(path) as f:
        for line in f:
            try:
                if json.loads(line)["t"] >= cutoff:
                    kept.append(line if line.endswith("\n") else line + "\n")
            except (ValueError, KeyError, TypeError):
                continue
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(kept)
    os.replace(tmp, path)


def window_stats(rows, lo, hi):
    """{id: {n, wh_day, active}}. wh_day = medel Wh/timme * 24 (Wh per dygn)."""
    by = {}
    for r in rows:
        if lo <= r["t"] < hi:
            by.setdefault(r["id"], []).append(r["wh"])
    return {i: {"n": len(v), "wh_day": sum(v) / len(v) * 24,
                "active": sum(1 for x in v if x > 0) / len(v)}
            for i, v in by.items()}


def find_anomalies(rows, now_ms, factor=1.5, min_extra_wh=1000.0,
                   min_base_wh=500.0):
    """Returnerar (avvikelser, antal dygn med historik).

    Jämför senaste dygnet med elementets egen median de föregående dygnen,
    och med övriga elements förändring (kalla dagar höjer alla)."""
    import statistics as st
    wins = [window_stats(rows, now_ms - (k + 1) * DAY_MS, now_ms - k * DAY_MS)
            for k in range(8)]
    ok = lambda w, i: i in w and w[i]["n"] >= MIN_SAMPLES_PER_DAY
    history = sum(1 for w in wins[1:] if any(ok(w, i) for i in w))
    today = {i: v for i, v in wins[0].items() if ok(wins[0], i)}
    if history < MIN_HISTORY_DAYS or not today:
        return [], history
    base = {}
    for i in today:
        past = [w[i] for w in wins[1:] if ok(w, i)]
        # Normalvärde nära noll (element som varit avstängda) går inte att
        # jämföra mot: då är det ofta bara säsongsstart, inte ett fel.
        if (len(past) >= MIN_HISTORY_DAYS
                and st.median(x["wh_day"] for x in past) >= min_base_wh):
            base[i] = (st.median(x["wh_day"] for x in past),
                       st.median(x["active"] for x in past))
    if not base:
        return [], history
    ratio = {i: (today[i]["wh_day"] + 50) / (base[i][0] + 50) for i in base}
    d_act = {i: today[i]["active"] - base[i][1] for i in base}
    peer_ratio = max(1.0, st.median(ratio.values()))
    peer_act = st.median(d_act.values())
    out = []
    for i in base:
        extra = today[i]["wh_day"] - base[i][0]
        high_energy = extra > min_extra_wh and ratio[i] > factor * peer_ratio
        high_active = (today[i]["active"] >= 0.5
                       and d_act[i] - peer_act >= 0.25)
        if high_energy or high_active:
            out.append({"id": i, "energy": high_energy, "active": high_active,
                        "wh_day": today[i]["wh_day"], "base_wh": base[i][0],
                        "act": today[i]["active"], "base_act": base[i][1]})
    return out, history


def anomaly_lines(anoms, history, rows):
    names = {}
    for r in rows:
        if r.get("name"):
            names[r["id"]] = r["name"]
    if history < MIN_HISTORY_DAYS:
        return [f"Elementkontroll: samlar data ({history}/{MIN_HISTORY_DAYS} dygn)"]
    if not anoms:
        return ["Elementkontroll: inga avvikelser"]
    out = ["⚠ Avvikande element (senaste dygnet):"]
    for a in anoms:
        what = []
        if a["energy"]:
            what.append(f"{fmt_num(a['wh_day'] / 1000, 1)} kWh, normalt "
                        f"{fmt_num(a['base_wh'] / 1000, 1)} kWh")
        if a["active"]:
            what.append(f"aktivt {a['act'] * 100:.0f} % av tiden, normalt "
                        f"{a['base_act'] * 100:.0f} %")
        out.append(f"  {names.get(a['id'], 'element ' + str(a['id']))}: "
                   + "; ".join(what))
    return out


def seconds_until_next(now=None):
    now = now or dt.datetime.now(TZ)
    nxt = now.replace(hour=SEND_AT.hour, minute=SEND_AT.minute,
                      second=0, microsecond=0)
    if nxt <= now:
        nxt = (nxt + dt.timedelta(days=1)).replace(
            hour=SEND_AT.hour, minute=SEND_AT.minute)
    # timestamp() tar hänsyn till sommartidsbyte; (nxt - now) gör det inte.
    return nxt.timestamp() - now.timestamp()


def debug_energy(env):
    """Rå energy_log per rum, samt jämförelse mellan Adax dygnssumma och
    egna prover för senaste avslutade dygnet (validerar own_energy)."""
    token = adax_token(env)
    now_ms = time.time() * 1000
    start = window_start()
    state = load_state(env)
    rows = load_samples(env, now_ms)
    for r in adax_content(token).get("rooms", []):
        pts = adax_points(token, r["id"])
        res = energy_since(pts, start, now_ms, state.get(str(r["id"]), []))
        print(f"{r['name']} (id {r['id']}): punkter={len(pts)} "
              f"sedan_start_Wh={res[0]:.0f} uppskattat={res[1]} nu={res[2]}")
        for p in (pts[:2] + pts[-2:]) if pts else []:
            print("   ", p)
        done = [p for p in pts if int(p["toTime"]) <= now_ms]
        if done:
            last = max(done, key=lambda p: int(p["toTime"]))
            own = own_energy(rows, int(last["fromTime"]), int(last["toTime"]),
                             room=r["id"])
            print(f"    senaste avslutade dygn: Adax={last['energyWh']} Wh, "
                  f"egna prover={'saknas' if own is None else f'{own:.0f} Wh'}")
    print(f"egen energi sedan start (alla rum): {own_energy(rows, start, now_ms)}")
    print(f"start_ms={start} nu_ms={int(now_ms)} tillstånd={state_path(env)}")


def debug_devices(env):
    """Visar råsvaret från /content/?withEnergy=1 (enheter och timenergi)."""
    token = adax_token(env)
    j = http_json(f"{ADAX}/rest/v1/content/?withEnergy=1",
                  headers={"Authorization": f"Bearer {token}"})
    print("nycklar:", sorted(j))
    now = time.time() * 1000
    for d in j.get("devices", []):
        et = d.get("energyTime")
        age = f"{(now - int(et)) / 60000:.0f} min sedan" if et else "saknas"
        print(f"  {d.get('name')} id={d.get('id')} energyWh={d.get('energyWh')} "
              f"energyTime={et} ({age}) övriga={sorted(set(d) - {'name','id','energyWh','energyTime'})}")
    print("rumsnycklar:", sorted({k for r in j.get("rooms", []) for k in r}))


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
    ap.add_argument("--debug-devices", action="store_true")
    ap.add_argument("--debug-energy", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    env = os.environ
    if a.debug_devices:
        debug_devices(env)
        return
    if a.debug_price:
        debug_price(env)
        return
    if a.debug_energy:
        debug_energy(env)
        return
    if a.once or a.dry_run:
        run_once(env, dry=a.dry_run)
        return
    next_send = time.time() + seconds_until_next()
    next_sample = time.time()
    while True:
        time.sleep(max(min(next_send, next_sample) - time.time(), 0) + 0.5)
        now = time.time()
        if now >= next_sample:
            try:
                sample_devices(env)
            except Exception as e:  # provtagning får aldrig stoppa notisen
                print(f"Provtagning misslyckades: {e}", file=sys.stderr)
            next_sample = time.time() + SAMPLE_EVERY
        if now >= next_send:
            try:
                run_once(env)
            except Exception as e:  # håll loopen vid liv
                print(f"Fel: {e}", file=sys.stderr)
            next_send = time.time() + 61 + seconds_until_next(
                dt.datetime.now(TZ) + dt.timedelta(seconds=61))


if __name__ == "__main__":
    main()

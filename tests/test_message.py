import datetime as dt
import tempwatch as o


def test_message_format():
    m = o.build_message(3.04, 21.5, [("Kök", 10.4, 10.0), ("Sovrum", 18.0, 17.5),
                                     ("Hall", 12.0, None)])
    assert m.splitlines() == [
        "Offerberg temp", "Utomhus: 3,0 °C", "Inomhus (köket): 21,5 °C",
        "Dessa rum har värme på:", "  Kök: 10,4 °C (mål 10 °C)",
        "  Sovrum: 18,0 °C (mål 17,5 °C)", "  Hall: 12,0 °C"]


def test_no_rooms():
    assert "(inga)" in o.build_message(1, 2, [])


def test_next_run_before_and_after_0730():
    tz = o.TZ
    before = dt.datetime(2026, 11, 3, 7, 0, tzinfo=tz)
    after = dt.datetime(2026, 11, 3, 8, 0, tzinfo=tz)
    assert o.seconds_until_next(before) == 30 * 60
    assert o.seconds_until_next(after) == 23.5 * 3600


def test_heating_rooms():
    j = {"rooms": [
        {"name": "A", "heatingEnabled": True, "temperature": 2150,
         "targetTemperature": 2100},
        {"name": "B", "heatingEnabled": False, "temperature": 1800},
        {"name": "C", "heatingEnabled": True}]}
    assert o.heating_rooms(j) == [("A", 21.5, 21.0)]


def test_telegram_html_bold_title_and_escape():
    assert o.telegram_html("Offerberg temp\nA < B") == "<b>Offerberg temp</b>\nA &lt; B"


def test_token_names():
    assert o.telegram_token({"TELEGRAM_TOKEN": " x "}) == "x"
    assert o.telegram_token({"TELEGRAM_BOT_TOKEN": "y"}) == "y"


H = 3600 * 1000
D = 24 * H
# tis 2026-11-03, dygnet börjar kl 04:00 (03:00 UTC), nu = 07:30 lokal tid.
TODAY = 1793674800000
NOW = TODAY + int(3.5 * H)
START = NOW - D  # igår 07:30
YEST = TODAY - D


def test_energy_since_exact_with_snapshot():
    pts = [{"fromTime": YEST, "toTime": TODAY, "energyWh": 24000},
           {"fromTime": TODAY, "toTime": TODAY + D, "energyWh": 700},
           {"fromTime": TODAY, "toTime": TODAY + D, "energyWh": 700}]  # dubblett
    snaps = [{"at": START + 60000, "from": YEST, "wh": 3000}]
    wh, est, cur = o.energy_since(pts, START, NOW, snaps)
    assert (wh, est) == (24000 - 3000 + 700, False)
    assert cur == {"from": TODAY, "wh": 700}


def test_energy_since_estimates_without_snapshot():
    pts = [{"fromTime": YEST, "toTime": TODAY, "energyWh": 24000},
           {"fromTime": TODAY, "toTime": TODAY + D, "energyWh": 700}]
    wh, est, _ = o.energy_since(pts, START, NOW)
    assert est is True and abs(wh - (24000 - 3500 + 700)) < 1e-6


def test_energy_since_ignores_old_and_empty():
    old = [{"fromTime": YEST - D, "toTime": YEST, "energyWh": 9999}]
    assert o.energy_since(old, START, NOW) == (0.0, False, None)
    assert o.energy_since([], START, NOW) == (0.0, False, None)


def test_window_start_is_yesterday_0730():
    import datetime as dt
    now = dt.datetime(2026, 11, 3, 7, 30, tzinfo=o.TZ)
    got = dt.datetime.fromtimestamp(o.window_start(now) / 1000, o.TZ)
    assert (got.day, got.hour, got.minute) == (2, 7, 30)


def test_state_roundtrip():
    import tempfile, os
    env = {"TEMPWATCH_STATE": os.path.join(tempfile.mkdtemp(), "s.json")}
    assert o.load_state(env) == {}
    o.save_state(env, {"1": [{"at": 1, "from": 2, "wh": 3}]})
    assert o.load_state(env) == {"1": [{"at": 1, "from": 2, "wh": 3}]}


def test_energy_line_with_and_without_price():
    import datetime as dt
    now = dt.datetime(2026, 11, 3, 7, 30, tzinfo=o.TZ)
    start = int(dt.datetime(2026, 11, 2, 7, 30, tzinfo=o.TZ).timestamp() * 1000)
    assert o.energy_line(12345, 1.2, start, now) == \
        "Energiförbrukning sedan igår 07:30: 12,3 kWh (≈ 14,81 kr)"
    assert o.energy_line(12345, 1.2, start, now, estimated=True).endswith("– uppskattat")
    assert o.energy_line(12345, None, None, now) == \
        "Energiförbrukning: 12,3 kWh (elpris ej inställt)"


def test_parse_price():
    assert o.parse_price("1,25") == 1.25
    assert o.parse_price(' "2.5" ') == 2.5
    assert o.parse_price("") is None and o.parse_price(None) is None


def test_message_includes_energy():
    m = o.build_message(1, 2, [], "Energi: 1,0 kWh")
    assert m.splitlines()[-1] == "Energi: 1,0 kWh"


def test_spot_average_and_price(monkeypatch=None):
    import datetime as dt
    rows = [{"time_start": "2026-11-03T05:00:00+01:00", "SEK_per_kWh": 0.40},
            {"time_start": "2026-11-03T06:00:00+01:00", "SEK_per_kWh": 0.60},
            {"time_start": "2026-11-03T23:00:00+01:00", "SEK_per_kWh": 9.00}]  # efter slutet
    orig = o.http_json
    o.http_json = lambda url, **k: rows
    try:
        start = int(dt.datetime(2026, 11, 3, 4, 0, tzinfo=o.TZ).timestamp() * 1000)
        now = int(dt.datetime(2026, 11, 3, 7, 30, tzinfo=o.TZ).timestamp() * 1000)
        assert abs(o.spot_average("SE4", start, now) - 0.5) < 1e-9
        price, note = o.price_per_kwh(
            {"ELECTRICITY_AREA": "se4", "ELECTRICITY_ADDON_SEK_PER_KWH": "0,50"}, start, now)
        assert abs(price - (0.5 + 0.5) * 1.25) < 1e-9 and "SE4" in note
    finally:
        o.http_json = orig


def test_fixed_price_fallback():
    assert o.price_per_kwh({"ELECTRICITY_PRICE_SEK_PER_KWH": "1,8"}, 1, 2) == (1.8, None)


def test_missing_spot_raises_instead_of_silent_none():
    orig = o.http_json
    o.http_json = lambda url, **k: []
    try:
        try:
            o.price_per_kwh({"ELECTRICITY_AREA": "SE2"}, 1793674800000, 1793716591783)
            assert False, "borde ha kastat"
        except RuntimeError as e:
            assert "SE2" in str(e)
    finally:
        o.http_json = orig


def test_energy_line_flags_missing_price():
    assert o.energy_line(5200, None).endswith("(elpris ej inställt)")


def test_energy_line_has_no_obs_text():
    line = o.energy_line(5200, 1.0, None, None, estimated=False)
    assert "OBS" not in line
    assert o.energy_line(5200, 1.0, None, None, estimated=True).endswith("– uppskattat")

def test_user_agent_is_sent():
    import urllib.request
    seen = {}
    class R:
        def __enter__(s): return s
        def __exit__(s, *a): pass
        def read(s): return b"[]"
    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=0: (seen.update(ua=req.get_header("User-agent")), R())[1]
    try:
        o.http_json("https://example.org/x")
    finally:
        urllib.request.urlopen = orig
    assert seen["ua"].startswith("tempwatch/")


def test_sample_rows():
    j = {"devices": [{"id": 1, "roomId": 9, "energyWh": 242, "energyTime": 5, "name": "x"}]}
    assert o.sample_rows(j, 10.9) == [{"t": 10, "id": 1, "room": 9, "name": "x", "wh": 242, "et": 5}]
    assert o.sample_rows({}, 1) == []


def _rows(days, per_day_means, now, id_names=None):
    """Syntetiska prover var 15:e minut i `days` dygn bakåt. per_day_means[k][id]
    = (wh, andel_aktiva) för dygn k (0 = senaste)."""
    rows = []
    for k in range(days):
        for j in range(96):
            t = now - k * D - j * 15 * 60 * 1000 - 1000
            for i, (wh, share) in per_day_means[k].items():
                active = (j / 96) < share
                rows.append({"t": t, "id": i, "et": t, "name": f"E{i}",
                             "wh": wh if active else 0.0})
    return rows


def test_anomaly_flags_outlier_but_not_cold_day():
    now = NOW
    normal = {1: (100, 0.5), 2: (100, 0.5), 3: (100, 0.5)}
    # Element 1 dubblar sin förbrukning, övriga oförändrade -> flaggas.
    today = {1: (400, 0.9), 2: (100, 0.5), 3: (100, 0.5)}
    rows = _rows(6, [today] + [normal] * 5, now)
    anoms, hist = o.find_anomalies(rows, now)
    assert hist == 5 and [a["id"] for a in anoms] == [1]
    # Kall dag: alla tredubblar -> ingen sticker ut.
    cold = {i: (300, 0.5) for i in (1, 2, 3)}
    rows = _rows(6, [cold] + [normal] * 5, now)
    assert o.find_anomalies(rows, now)[0] == []


def test_anomaly_needs_history():
    now = NOW
    d = {1: (500, 1.0)}
    anoms, hist = o.find_anomalies(_rows(2, [d, d], now), now)
    assert anoms == [] and hist < 3
    assert "samlar data" in o.anomaly_lines(anoms, hist, [])[0]


def test_anomaly_lines_text():
    a = {"id": 7, "energy": True, "active": True, "wh_day": 4100.0,
         "base_wh": 1900.0, "act": 0.85, "base_act": 0.4}
    lines = o.anomaly_lines([a], 5, [{"id": 7, "name": "Mot älven"}])
    assert lines[0].startswith("⚠") and "Mot älven" in lines[1]
    assert "4,1 kWh, normalt 1,9 kWh" in lines[1] and "85 %" in lines[1]


def test_load_samples_skips_bad_and_dupes():
    import tempfile, os, json
    d = tempfile.mkdtemp()
    env = {"TEMPWATCH_STATE": os.path.join(d, "s.json")}
    ok = {"t": NOW, "id": 1, "et": 5, "wh": 3}
    with open(os.path.join(d, "devices.jsonl"), "w") as f:
        f.write(json.dumps(ok) + "\n" + "inte json\n" + json.dumps(ok) + "\n"
                + json.dumps({"t": NOW, "id": 2, "et": 5, "wh": None}) + "\n")
    assert len(o.load_samples(env, NOW + 1)) == 1


def test_energy_line_has_no_spot_line():
    assert "Snittspot" not in o.energy_line(1000, 1.0, None, None)


def test_idle_baseline_is_not_flagged_as_anomaly():
    # Elementen var nästan avstängda i veckan före; nu börjar värmen -> inget fel.
    now = NOW
    idle = {1: (1, 0.1), 2: (1, 0.1), 3: (1, 0.1)}
    winter = {1: (400, 0.9), 2: (500, 0.9), 3: (300, 0.98)}
    rows = _rows(6, [winter] + [idle] * 5, now)
    assert o.find_anomalies(rows, now)[0] == []


def test_http_json_retries_5xx_then_succeeds():
    import io, urllib.error, urllib.request
    calls = {"n": 0}
    class R:
        def __enter__(s): return s
        def __exit__(s, *a): pass
        def read(s, *a): return b'{"ok": 1}'
    def fake(req, timeout=0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 502, "bad", {}, io.BytesIO(b"boom"))
        return R()
    orig_open, orig_sleep = urllib.request.urlopen, o.time.sleep
    urllib.request.urlopen, o.time.sleep = fake, lambda s: None
    try:
        assert o.http_json("https://example.org/x") == {"ok": 1}
        assert calls["n"] == 3
        calls["n"] = -10  # alltid 502
        try:
            o.http_json("https://example.org/x", retries=1)
            assert False
        except RuntimeError as e:
            assert "HTTP 502" in str(e)
    finally:
        urllib.request.urlopen, o.time.sleep = orig_open, orig_sleep


def test_http_json_4xx_is_not_retried():
    import io, urllib.error, urllib.request
    calls = {"n": 0}
    def fake(req, timeout=0):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b"x"))
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake
    try:
        try:
            o.http_json("https://example.org/x")
        except RuntimeError:
            pass
        assert calls["n"] == 1
    finally:
        urllib.request.urlopen = orig


# ---------- sommartid ----------

def test_next_send_correct_across_dst_changes():
    for now, nxt in [
            (dt.datetime(2026, 10, 24, 7, 40, tzinfo=o.TZ),   # 25 h-dygn (hösten)
             dt.datetime(2026, 10, 25, 7, 30, tzinfo=o.TZ)),
            (dt.datetime(2027, 3, 27, 7, 40, tzinfo=o.TZ),    # 23 h-dygn (våren)
             dt.datetime(2027, 3, 28, 7, 30, tzinfo=o.TZ))]:
        assert o.seconds_until_next(now) == nxt.timestamp() - now.timestamp()


# ---------- egen energi från prover ----------

HOUR = 3_600_000


def _saw(n_hours, watts, end=NOW, room_of=lambda i: i // 10):
    """Prov som Adax ger dem: energin sedan hel timme (sågtand), vid minut
    13, 28, 43, 58. watts = {element: Wh per timme vid konstant effekt}."""
    rows = []
    for h in range(int(end // HOUR) - n_hours - 1, int(end // HOUR) + 1):
        for minute in (13, 28, 43, 58):
            t = h * HOUR + minute * 60000
            if end - n_hours * HOUR <= t < end:
                for i, p in watts.items():
                    rows.append({"t": t, "id": i, "et": t, "room": room_of(i),
                                 "wh": p * minute / 60})
    return rows


def test_own_energy_sums_hourly_maxima_not_mean_of_sawtooth():
    rows = _saw(26, {11: 100, 12: 50})
    wh, est = o.own_energy(rows, NOW - 24 * HOUR, NOW)
    true = 150 * 24                       # 3600 Wh
    assert 0.94 * true < wh < true        # :58-provet ligger ett par minuter under full timme
    assert est is False
    # Det gamla sättet (medelvärde x 24) hade gett ungefär hälften:
    mean = sum(r["wh"] for r in rows) / len(rows)
    assert mean * 24 * 2 < 0.6 * true


def test_own_energy_filters_room():
    rows = _saw(26, {11: 100, 21: 50})
    wh, _ = o.own_energy(rows, NOW - 24 * HOUR, NOW, room=1)
    assert 0.94 * 2400 < wh < 2400


def test_own_energy_none_when_data_thin():
    assert o.own_energy(_saw(5, {11: 100}), NOW - 24 * HOUR, NOW) is None
    assert o.own_energy([], NOW - 24 * HOUR, NOW) is None


def test_own_energy_fills_few_missing_hours_and_flags_estimate():
    rows = [r for r in _saw(26, {11: 100})
            if not (NOW - 12 * HOUR <= r["t"] < NOW - 10 * HOUR)]  # två timmar saknas
    wh, est = o.own_energy(rows, NOW - 24 * HOUR, NOW)
    assert est is True and 0.94 * 2400 < wh < 2400


def test_hourly_totals_takes_max_in_hour():
    rows = [{"t": TODAY + 3 * 60000, "id": 1, "wh": 0.0},
            {"t": TODAY + 28 * 60000, "id": 1, "wh": 250.0},
            {"t": TODAY + 58 * 60000, "id": 1, "wh": 495.0}]
    assert o.hourly_totals(rows) == {1: {TODAY // HOUR: (495.0, 58.0)}}


def test_next_sample_time_is_aligned_to_13_28_43_58():
    import datetime as dt
    base = dt.datetime(2026, 11, 10, 7, 0, 5, tzinfo=dt.timezone.utc).timestamp()
    got = [o.next_sample_time(base)]
    for _ in range(4):
        got.append(o.next_sample_time(got[-1]))
    minutes = [int(g // 60) % 60 for g in got]
    assert minutes == [13, 28, 43, 58, 13]
    assert all(g % 60 == 0 for g in got)


def test_prune_samples_keeps_recent_only():
    import tempfile, os, json
    d = tempfile.mkdtemp()
    env = {"TEMPWATCH_STATE": os.path.join(d, "s.json")}
    path = os.path.join(d, "devices.jsonl")
    with open(path, "w") as f:
        for age_days in (20, 15, 13, 1):
            f.write(json.dumps({"t": NOW - age_days * D, "id": 1}) + "\n")
        f.write("trasig\n")
    o.prune_samples(env, NOW, keep_days=14)
    kept = [json.loads(x)["t"] for x in open(path)]
    assert kept == [NOW - 13 * D, NOW - 1 * D]


# ---------- Telegram ----------

def test_telegram_send_retries_via_http_json():
    calls = []
    def fake(url, data=None, headers=None, timeout=20, retries=2):
        calls.append((url, data, retries))
        return {"ok": True}
    orig = o.http_json
    o.http_json = fake
    try:
        o.telegram_send({"TELEGRAM_TOKEN": " tok ", "TELEGRAM_CHAT_ID": "42"},
                        "Rubrik\nA < B")
    finally:
        o.http_json = orig
    url, data, retries = calls[0]
    assert url == "https://api.telegram.org/bottok/sendMessage"
    assert data["chat_id"] == "42" and data["parse_mode"] == "HTML"
    assert data["text"] == "<b>Rubrik</b>\nA &lt; B" and retries >= 3


def test_telegram_send_requires_credentials():
    try:
        o.telegram_send({}, "x")
        assert False
    except RuntimeError as e:
        assert "saknas" in str(e)


# ---------- hela flödet med mockade anrop ----------

def _run(env, samples, adax_down=False, eco_down=False):
    """Kör run_once med mockade nätverksanrop. Returnerar (meddelande, anrop)."""
    import tempfile, os, json, io, contextlib
    d = tempfile.mkdtemp()
    env = {**env, "TEMPWATCH_STATE": os.path.join(d, "s.json")}
    with open(os.path.join(d, "devices.jsonl"), "w") as f:
        for r in samples:
            f.write(json.dumps(r) + "\n")
    calls = []
    saved = {k: getattr(o, k) for k in (
        "ecowitt_temps", "adax_token", "adax_content", "adax_points",
        "spot_average", "telegram_send", "window_start")}
    def eco(e):
        if eco_down: raise RuntimeError("nere")
        return (-6.0, 16.6)
    def tok(e):
        if adax_down: raise RuntimeError("nere")
        return "T"
    o.ecowitt_temps = eco
    o.adax_token = tok
    o.adax_content = lambda t: {"rooms": [
        {"id": 1, "name": "Kök", "heatingEnabled": True, "temperature": 1680,
         "targetTemperature": 1800}]}
    o.adax_points = lambda t, r: (calls.append("adax_points") or [])
    o.spot_average = lambda a, s, n: 0.10
    o.telegram_send = lambda e, text: calls.append(("send", text))
    o.window_start = lambda now=None: int(NOW_REAL - 24 * H)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            o.run_once(env, dry=False)
    finally:
        for k, v in saved.items():
            setattr(o, k, v)
    sent = [c[1] for c in calls if isinstance(c, tuple)]
    return sent[0], calls


import time as _time
NOW_REAL = _time.time() * 1000
PRICE_ENV = {"ELECTRICITY_AREA": "SE2", "ELECTRICITY_ADDON_SEK_PER_KWH": "0,6789"}


def test_run_once_uses_own_samples_and_skips_adax_energy_log():
    rows = _saw(26, {11: 100, 12: 50}, end=int(NOW_REAL), room_of=lambda i: 1)
    msg, calls = _run(PRICE_ENV, rows)
    lines = msg.splitlines()
    assert lines[:3] == ["Offerberg temp", "Utomhus: -6,0 °C", "Inomhus (köket): 16,6 °C"]
    assert "  Kök: 16,8 °C (mål 18 °C)" in lines
    # ca 150 Wh/h * 24 h = 3,6 kWh (några % lägre av provens läge i timmen)
    import re
    e = [l for l in lines if l.startswith("Energiförbrukning sedan igår")][0]
    kwh = float(re.search(r"([\d,]+) kWh", e).group(1).replace(",", "."))
    assert 3.3 <= kwh <= 3.7 and "uppskattat" not in e and "kr)" in e
    assert "adax_points" not in calls          # inga energy_log-anrop behövs
    assert not any("⚠" in l and "otillgänglig" in l for l in lines)


def test_run_once_falls_back_to_adax_when_samples_missing():
    msg, calls = _run(PRICE_ENV, [])
    assert "adax_points" in calls
    assert "Elementkontroll: samlar data" in msg


def test_run_once_energy_survives_adax_outage():
    rows = _saw(26, {11: 100}, end=int(NOW_REAL), room_of=lambda i: 1)
    msg, _ = _run(PRICE_ENV, rows, adax_down=True)
    assert "Energiförbrukning" in msg and "Adax otillgänglig" in msg
    assert "okänt" in msg                       # rummen är okända


def test_run_once_sends_when_everything_is_down():
    msg, _ = _run({}, [], adax_down=True, eco_down=True)
    assert msg.startswith("Offerberg temp") and "Ecowitt otillgänglig" in msg


def test_adax_energy_tolerates_one_failing_room():
    orig = o.adax_points
    def fake(token, rid):
        if rid == 2:
            raise RuntimeError("HTTP 502")
        return [{"fromTime": TODAY, "toTime": TODAY + D, "energyWh": 700}]
    o.adax_points = fake
    try:
        wh, est, new = o.adax_energy({}, "T", [1, 2], START, NOW, {})
    finally:
        o.adax_points = orig
    assert wh == 700 and est is True and set(new) == {"1", "2"}


def test_minute_bins_shows_sawtooth():
    # Sågtand: energin sedan hel timme växer med minut i timmen.
    hour = 1793674800000  # hel timme
    rows = [{"t": hour + m * 60000, "id": 1, "wh": float(m)} for m in (1, 16, 31, 46)]
    b = o.minute_bins(rows)
    assert [round(v[1]) for v in b.values()] == [1, 16, 31, 46]
    assert list(b) == ["00-14", "15-29", "30-44", "45-59"]


# ---------- frostlarm ----------

def test_decide_alert_lifecycle():
    n, h = 1_000_000_000_000, 3_600_000
    act, st = o.decide_alert(9.5, {}, n, below=8)
    assert act is None and st == {}                       # varmt: inget
    act, st = o.decide_alert(7.4, st, n, below=8)
    assert act == "alarm" and st["active"] and st["min"] == 7.4
    act, st = o.decide_alert(6.9, st, n + h, below=8)     # fortfarande kallt, för tidigt
    assert act is None and st["min"] == 6.9
    act, st = o.decide_alert(7.0, st, n + 6 * h, below=8)
    assert act == "remind" and st["last_sent"] == n + 6 * h
    act, st = o.decide_alert(8.4, st, n + 7 * h, below=8)  # över gränsen men inom hysteres
    assert act is None and st["active"]
    act, st = o.decide_alert(9.1, st, n + 8 * h, below=8)
    assert act == "clear" and st == {"active": False, "min": 6.9}
    assert o.decide_alert(9.3, st, n + 9 * h, below=8)[0] is None   # ingen ny signal


def _frost_env():
    import tempfile, os
    d = tempfile.mkdtemp()
    return {"TEMPWATCH_STATE": os.path.join(d, "s.json"),
            "TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}


def test_check_frost_sends_once_then_clears():
    env = _frost_env()
    sent, temps = [], [(-6.0, 7.5, None)]
    orig_e, orig_t = o.ecowitt_reading, o.telegram_send
    o.ecowitt_reading = lambda e: temps[0]
    o.telegram_send = lambda e, text: sent.append(text)
    try:
        n = 1_000_000_000_000
        assert o.check_frost(env, now_ms=n) == "alarm"
        assert o.check_frost(env, now_ms=n + 15 * 60000) is None      # ingen spam
        temps[0] = (-4.0, 9.2, None)
        assert o.check_frost(env, now_ms=n + 3_600_000) == "clear"
    finally:
        o.ecowitt_reading, o.telegram_send = orig_e, orig_t
    assert len(sent) == 2
    assert sent[0].startswith("⚠ Frostlarm") and "7,5 °C" in sent[0] and "Utomhus: -6,0 °C" in sent[0]
    assert sent[1].startswith("✅") and "7,5 °C" in sent[1]


def test_check_frost_retries_after_failed_send():
    env = _frost_env()
    calls = {"n": 0}
    def flaky(e, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Telegram nere")
    orig_e, orig_t = o.ecowitt_reading, o.telegram_send
    o.ecowitt_reading = lambda e: (-6.0, 5.0, None)
    o.telegram_send = flaky
    try:
        n = 1_000_000_000_000
        try:
            o.check_frost(env, now_ms=n)
            assert False
        except RuntimeError:
            pass
        assert o.check_frost(env, now_ms=n + 900_000) == "alarm"   # nytt försök, inte tyst
    finally:
        o.ecowitt_reading, o.telegram_send = orig_e, orig_t


def test_check_frost_threshold_from_env_and_dry_run_does_not_send():
    env = {**_frost_env(), "ALERT_INDOOR_BELOW_C": "5,5"}
    sent = []
    orig_e, orig_t = o.ecowitt_reading, o.telegram_send
    o.ecowitt_reading = lambda e: (-6.0, 7.0, None)
    o.telegram_send = lambda e, text: sent.append(text)
    try:
        assert o.check_frost(env, now_ms=1) is None          # 7 > 5,5
        env["ALERT_INDOOR_BELOW_C"] = "8"
        assert o.check_frost(env, dry=True, now_ms=1) == "alarm"
    finally:
        o.ecowitt_reading, o.telegram_send = orig_e, orig_t
    assert sent == []


# ---------- vakthund ----------

def test_decide_watch_needs_consecutive_failures_and_clears():
    n, h = 1_000_000_000_000, 3_600_000
    st = {}
    act, st = o.decide_watch("fail", st, n, limit=3)
    assert act is None and st["fails"] == 1
    act, st = o.decide_watch("ok", st, n + 900_000, limit=3)        # blipp: nollställs
    assert act is None and st == {"fails": 0, "active": False}
    for k in range(2):
        act, st = o.decide_watch("stale", st, n + k * 900_000, limit=3)
        assert act is None
    act, st = o.decide_watch("stale", st, n + 2 * 900_000, limit=3)
    assert act == "alarm" and st["active"]
    act, st = o.decide_watch("stale", st, n + 3 * 900_000, limit=3)  # ingen spam
    assert act is None
    act, st = o.decide_watch("fail", st, n + 13 * h, limit=3, repeat_h=12)
    assert act == "remind"
    act, st = o.decide_watch("ok", st, n + 14 * h, limit=3)
    assert act == "clear" and st == {"fails": 0, "active": False}


def test_epoch_ms_handles_seconds_ms_strings_and_junk():
    assert o.epoch_ms("1793717721") == 1793717721000
    assert o.epoch_ms(1793717721410) == 1793717721410
    assert o.epoch_ms(None) is None and o.epoch_ms("x") is None and o.epoch_ms(0) is None


def _watch_run(readings, now_ms, env=None, sent=None):
    env = env or _frost_env()
    sent = [] if sent is None else sent
    orig_e, orig_t = o.ecowitt_reading, o.telegram_send
    it = iter(readings)
    def fake(e):
        v = next(it)
        if isinstance(v, Exception):
            raise v
        return v
    o.ecowitt_reading, o.telegram_send = fake, lambda e, text: sent.append(text)
    return env, sent, orig_e, orig_t


def test_watchdog_alarms_after_three_failures_and_recovers():
    n = 1_700_000_000_000
    env, sent, oe, ot = _watch_run([RuntimeError("HTTP 502")] * 3 + [(-5.0, 12.0, n + 4 * 900_000)], n)
    try:
        for k in range(3):
            o.check_frost(env, now_ms=n + k * 900_000)
        assert len(sent) == 1 and sent[0].startswith("⚠ Frostlarmet är blint")
        assert "HTTP 502" in sent[0] and "3 gånger" in sent[0]
        o.check_frost(env, now_ms=n + 4 * 900_000)
        assert len(sent) == 2 and sent[1].startswith("✅")
    finally:
        o.ecowitt_reading, o.telegram_send = oe, ot


def test_watchdog_detects_stale_sensor_and_skips_frost_logic():
    n = 1_700_000_000_000
    old = n - 3 * 3_600_000                  # senaste mätning för 3 h sedan, kallt värde
    env, sent, oe, ot = _watch_run([(-5.0, 4.0, old)] * 3, n)
    try:
        for k in range(3):
            assert o.check_frost(env, now_ms=n + k * 900_000) is None   # inget frostlarm på gammal data
        assert len(sent) == 1 and "inte rapporterat" in sent[0]
    finally:
        o.ecowitt_reading, o.telegram_send = oe, ot


def test_watchdog_alarm_retried_if_telegram_fails():
    n = 1_700_000_000_000
    env = _frost_env()
    state = {"n": 0}
    oe, ot = o.ecowitt_reading, o.telegram_send
    def boom(e):
        raise RuntimeError("nere")
    def flaky(e, text):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("Telegram nere")
    o.ecowitt_reading, o.telegram_send = boom, flaky
    try:
        o.check_frost(env, now_ms=n)
        o.check_frost(env, now_ms=n + 900_000)
        try:
            o.check_frost(env, now_ms=n + 2 * 900_000)      # larm, men Telegram faller
            assert False
        except RuntimeError:
            pass
        o.check_frost(env, now_ms=n + 3 * 900_000)           # försöks igen
        assert state["n"] == 2
    finally:
        o.ecowitt_reading, o.telegram_send = oe, ot


def test_ping_ignores_failures_and_empty_url():
    import urllib.request
    o.ping("")                                               # ingen krasch, inget anrop
    orig = urllib.request.urlopen
    def boom(req, timeout=0):
        raise OSError("nere")
    urllib.request.urlopen = boom
    try:
        o.ping("https://hc-ping.example/abc")
    finally:
        urllib.request.urlopen = orig

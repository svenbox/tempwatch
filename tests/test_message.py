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


def test_covered_ms_detects_missing_day():
    # Gårdagens dygn saknas: bara dagens dygn finns -> täckning = nu - dagens start.
    pts = [{"fromTime": TODAY, "toTime": TODAY + D, "energyWh": 5}]
    assert o.covered_ms(pts, START, NOW) == NOW - TODAY
    full = pts + [{"fromTime": YEST, "toTime": TODAY, "energyWh": 5}]
    assert o.covered_ms(full + full, START, NOW) == NOW - START  # dubbletter ok


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
    assert "Snittspot" not in o.energy_line(1000, 1.0, None, None, note="x")

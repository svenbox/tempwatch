import datetime as dt
import tempwatch as o


def test_message_format():
    m = o.build_message(3.04, 21.5, [("Vardagsrum", 20.99), ("Sovrum", 18.0)])
    assert m.splitlines() == [
        "Offerberg temp", "Utomhus: 3,0 °C", "Inomhus: 21,5 °C",
        "Dessa rum har värme på:", "  Vardagsrum: 21,0 °C", "  Sovrum: 18,0 °C"]


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
        {"name": "A", "heatingEnabled": True, "temperature": 2150},
        {"name": "B", "heatingEnabled": False, "temperature": 1800},
        {"name": "C", "heatingEnabled": True}]}
    assert o.heating_rooms(j) == [("A", 21.5)]


def test_telegram_html_bold_title_and_escape():
    assert o.telegram_html("Offerberg temp\nA < B") == "<b>Offerberg temp</b>\nA &lt; B"


def test_token_names():
    assert o.telegram_token({"TELEGRAM_TOKEN": " x "}) == "x"
    assert o.telegram_token({"TELEGRAM_BOT_TOKEN": "y"}) == "y"


def test_energy_last_24h_filters_window():
    now = 1_000_000_000_000
    h = 3600 * 1000
    pts = [{"toTime": now - 1 * h, "energyWh": "500"},
           {"toTime": now - 23 * h, "energyWh": 250},
           {"toTime": now - 25 * h, "energyWh": 9999},
           {"toTime": now + h, "energyWh": 9999}]
    assert o.energy_last_24h(pts, now) == 750


def test_energy_line_with_and_without_price():
    assert o.energy_line(12345, 1.2) == "Energi senaste 24 h: 12,3 kWh (≈ 14,81 kr)"
    assert o.energy_line(12345, None) == "Energi senaste 24 h: 12,3 kWh"


def test_parse_price():
    assert o.parse_price("1,25") == 1.25
    assert o.parse_price(' "2.5" ') == 2.5
    assert o.parse_price("") is None and o.parse_price(None) is None


def test_message_includes_energy():
    m = o.build_message(1, 2, [], "Energi senaste 24 h: 1,0 kWh")
    assert m.splitlines()[-1] == "Energi senaste 24 h: 1,0 kWh"

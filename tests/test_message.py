import datetime as dt
import offerberg_temp as o


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

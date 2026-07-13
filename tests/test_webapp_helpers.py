from webapp.app import interval_minutes, parse_options, shift_day, shift_month


def test_shift_day_within_month():
    assert shift_day("2026-07-15", 1) == "2026-07-16"
    assert shift_day("2026-07-15", -1) == "2026-07-14"


def test_shift_day_across_month_boundary():
    assert shift_day("2026-07-31", 1) == "2026-08-01"
    assert shift_day("2026-08-01", -1) == "2026-07-31"


def test_shift_month_within_year():
    assert shift_month("2026-05", 1) == "2026-06"
    assert shift_month("2026-05", -1) == "2026-04"


def test_shift_month_across_year_boundary():
    assert shift_month("2026-12", 1) == "2027-01"
    assert shift_month("2026-01", -1) == "2025-12"


def test_interval_minutes_normal_range():
    assert interval_minutes("09:00", "18:00") == 540


def test_interval_minutes_overnight_wraps():
    assert interval_minutes("22:00", "02:00") == 240


def test_interval_minutes_missing_times():
    assert interval_minutes(None, "10:00") == 0
    assert interval_minutes("10:00", None) == 0


def test_parse_options_splits_on_newlines_and_commas():
    assert parse_options("Бег\nВелосипед, Плавание") == ["Бег", "Велосипед", "Плавание"]
    assert parse_options("  \n ") == []

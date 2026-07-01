from bot.main import shift_month


def test_shift_month_within_year():
    assert shift_month("2026-05", 1) == "2026-06"
    assert shift_month("2026-05", -1) == "2026-04"


def test_shift_month_across_year_boundary():
    assert shift_month("2026-12", 1) == "2027-01"
    assert shift_month("2026-01", -1) == "2025-12"

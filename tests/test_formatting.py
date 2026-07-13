from bot.formatting import escape_md, sparkline


def test_escape_md_escapes_special_chars():
    assert escape_md("1.5") == "1\\.5"
    assert escape_md("a-b_c") == "a\\-b\\_c"


def test_escape_md_handles_non_string_input():
    assert escape_md(61.5) == "61\\.5"


def test_sparkline_empty():
    assert sparkline([]) == ""


def test_sparkline_constant_values():
    assert sparkline([5, 5, 5]) == "▁▁▁"


def test_sparkline_varies_with_range():
    result = sparkline([1, 5, 10])
    assert len(result) == 3
    assert result[0] == "▁"
    assert result[-1] == "█"

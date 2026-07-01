from bot.formatting import escape_md, progress_bar


def test_escape_md_escapes_special_chars():
    assert escape_md("1.5") == "1\\.5"
    assert escape_md("a-b_c") == "a\\-b\\_c"


def test_escape_md_handles_non_string_input():
    assert escape_md(61.5) == "61\\.5"


def test_progress_bar_zero_total():
    assert progress_bar(0, 0) == "░" * 10 + " —"


def test_progress_bar_full():
    assert progress_bar(10, 10) == "█" * 10 + " 100%"


def test_progress_bar_partial():
    bar = progress_bar(5, 10)
    assert bar == "█████░░░░░ 50%"

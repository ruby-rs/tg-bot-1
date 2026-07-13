from bot.main import BOT_COMMANDS


def test_bot_commands_have_unique_names_and_descriptions():
    names = [c.command for c in BOT_COMMANDS]
    assert len(names) == len(set(names))
    assert all(c.description for c in BOT_COMMANDS)


def test_bot_is_minimal_secondary_channel():
    """The bot only covers panel toggling + weight; everything else (tasks,
    calendar, expenses, stats, tips) lives in the web interface."""
    names = {c.command for c in BOT_COMMANDS}
    assert names == {"panel", "weight", "clear", "cancel", "help"}

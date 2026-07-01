from bot.main import BOT_COMMANDS, QUICK_NAV_HANDLERS


def test_bot_commands_have_unique_names_and_descriptions():
    names = [c.command for c in BOT_COMMANDS]
    assert len(names) == len(set(names))
    assert all(c.description for c in BOT_COMMANDS)


def test_quick_nav_handlers_are_callable():
    assert set(QUICK_NAV_HANDLERS) == {"cats", "calendar", "habits", "expenses", "weightstats", "tips"}
    assert all(callable(handler) for handler in QUICK_NAV_HANDLERS.values())

MD_SPECIAL = r"_*[]()~`>#+-=|{}.!"


def escape_md(text: str) -> str:
    text = str(text)
    return "".join(f"\\{c}" if c in MD_SPECIAL else c for c in text)


def progress_bar(done: int, total: int, length: int = 10) -> str:
    if total == 0:
        return "░" * length + " —"
    filled = round(length * done / total)
    bar = "█" * filled + "░" * (length - filled)
    pct = round(100 * done / total)
    return f"{bar} {pct}%"

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


SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return SPARK_CHARS[0] * len(values)
    span = hi - lo
    return "".join(
        SPARK_CHARS[min(len(SPARK_CHARS) - 1, int((v - lo) / span * (len(SPARK_CHARS) - 1)))]
        for v in values
    )


def table(rows, headers=None) -> str:
    """Render a monospace table for wrapping in a ``` code block. rows is a list of tuples of str."""
    all_rows = ([headers] if headers else []) + list(rows)
    widths = [max(len(str(r[i])) for r in all_rows) for i in range(len(all_rows[0]))]
    lines = []
    for i, row in enumerate(all_rows):
        line = "  ".join(str(c).ljust(widths[j]) for j, c in enumerate(row))
        lines.append(line)
        if headers and i == 0:
            lines.append("  ".join("-" * widths[j] for j in range(len(widths))))
    return "\n".join(lines)

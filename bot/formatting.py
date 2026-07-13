MD_SPECIAL = r"_*[]()~`>#+-=|{}.!"


def escape_md(text: str) -> str:
    text = str(text)
    return "".join(f"\\{c}" if c in MD_SPECIAL else c for c in text)


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

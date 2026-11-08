"""Deterministic full-date localization check. Never normalizes metric values/units."""
from datetime import date
import re

MONTHS = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,"july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
MONTH_PATTERN = "|".join(MONTHS)
SOURCE_PATTERNS = (
    re.compile(rf"\b(?P<day>\d{{1,2}})\s+(?P<month>{MONTH_PATTERN})\s*,?\s*(?P<year>\d{{4}})\b",re.I),
    re.compile(rf"\b(?P<month>{MONTH_PATTERN})\s+(?P<day>\d{{1,2}})\s*,?\s*(?P<year>\d{{4}})\b",re.I),
)
CHINESE_DATE = re.compile(r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日")


def numeric_check_text(claim, quotes):
    """Only exempt the month digits IN an exactly supported full localized date.

    Any unrelated occurrence of that same number stays checked normally. Source
    triples must occur within one quote, never assembled from different snippets.
    """
    supported = set()
    for quote in quotes:
        for pattern in SOURCE_PATTERNS:
            for match in pattern.finditer(quote):
                year, month, day = int(match["year"]), MONTHS[match["month"].lower()], int(match["day"])
                try: date(year,month,day)
                except ValueError: continue
                supported.add((year,month,day))
    def replace(match):
        triple = tuple(int(match[k]) for k in ("year","month","day"))
        if triple not in supported: return match.group()
        # Keep the year/day digits; only the translated month name is non-numeric.
        relative_a = match.start("month")-match.start()
        relative_b = match.end("month")-match.start()
        text = match.group()
        return text[:relative_a]+"localized_month"+text[relative_b:]
    return CHINESE_DATE.sub(replace,claim)

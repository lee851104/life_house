"""Pure temporal split utilities used to prevent future-data leakage."""
from collections.abc import Iterable
from datetime import date


def split_by_date(records: Iterable[dict], cutoff: date, field: str = "occurred_on"):
    """Return train/test records without allowing cutoff-or-later data into train."""
    train, test = [], []
    for record in records:
        target = train if record[field] < cutoff else test
        target.append(record)
    return train, test

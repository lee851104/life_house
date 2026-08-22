from datetime import date

from src.data.validation import split_by_date


def test_future_observations_never_enter_training_split():
    cutoff = date(2026, 1, 1)
    rows = [
        {"occurred_on": date(2025, 12, 31), "id": "past"},
        {"occurred_on": cutoff, "id": "cutoff"},
        {"occurred_on": date(2026, 1, 2), "id": "future"},
    ]

    train, test = split_by_date(rows, cutoff)

    assert [row["id"] for row in train] == ["past"]
    assert {row["id"] for row in test} == {"cutoff", "future"}
    assert all(row["occurred_on"] < cutoff for row in train)

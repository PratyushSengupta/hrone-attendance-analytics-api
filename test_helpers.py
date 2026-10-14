
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from app.main import (
    compute_late_minutes,
    compute_work_hours,
    compute_overtime,
    IST,
)

# Exactly 10 minutes late: should be 0
assert compute_late_minutes(
    datetime(2026, 10, 11, 9, 40), "09:30"
) == 0

# 11 minutes late: should be 11
assert compute_late_minutes(
    datetime(2026, 10, 11, 9, 41), "09:30"
) == 11

# Work hours rounded to two decimal places
assert compute_work_hours(
    datetime(2026, 10, 11, 9, 30),
    datetime(2026, 10, 11, 18, 30),
) == 9.0

# Exactly 30 minutes overtime
assert compute_overtime(
    datetime(2026, 10, 11, 19, 0, tzinfo=IST),
    "18:30",
    "2026-10-11",
) == 30

print("All helper tests passed.")

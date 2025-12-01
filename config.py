from datetime import time, date

# Your local timezone
TIMEZONE = "America/New_York"

# Business hours per weekday (0=Monday ... 6=Sunday)
BUSINESS_HOURS = {
    0: (time(8, 0), time(19, 0)),  # Monday
    1: (time(8, 0), time(19, 0)),  # Tuesday
    2: (time(8, 0), time(19, 0)),  # Wednesday
    3: (time(8, 0), time(19, 0)),  # Thursday
    4: (time(8, 0), time(19, 0)),  # Friday
    5: (time(8, 0), time(19, 0)),  # Saturday
    6: (time(8, 0), time(19, 0)),  # Sunday
}

# Default job duration in minutes (you can later vary this by service_type)
DEFAULT_JOB_DURATION_MIN = 120

# Buffer between jobs, in minutes
DEFAULT_BUFFER_MIN = 30

# Dates you absolutely do NOT work
BLACKOUT_DATES = {
    # Example:
    # date(2025, 12, 25),
    # date(2025, 1, 1),
}

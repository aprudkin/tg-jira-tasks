"""Shared sync-channel interval contract (minutes)."""

MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 1440


def validate_interval(value: object) -> int:
    """Reject coercions, booleans and unsafe scheduler intervals."""
    if type(value) is not int or not MIN_INTERVAL_MINUTES <= value <= MAX_INTERVAL_MINUTES:
        raise ValueError(
            f"Interval must be an integer from {MIN_INTERVAL_MINUTES} "
            f"to {MAX_INTERVAL_MINUTES} minutes."
        )
    return value

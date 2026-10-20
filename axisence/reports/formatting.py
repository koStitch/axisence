"""The report's formats for money, dates, times, counts, percents, ages, and
distances.
"""

from datetime import date, datetime, time
from decimal import Decimal

from django.utils import dateformat, timezone

from axisence.research.models import Contact

# Code points, because ruff flags the literal characters.
EN_DASH = chr(0x2013)
PLUS_MINUS = chr(0x00B1)


def format_report_date(moment: datetime) -> str:
    """Return the local date of a moment, such as "October 20, 2026"."""
    return dateformat.format(timezone.localdate(moment), "F j, Y")


def format_short_date(value: date) -> str:
    """Return a date such as "Oct 14, 2026"."""
    return dateformat.format(value, "M j, Y")


def format_month_label(value: date) -> str:
    """Return a date's month such as "Oct 2026"."""
    return dateformat.format(value, "M Y")


def format_month(value: date) -> str:
    """Return a date's month such as "March 2026"."""
    return dateformat.format(value, "F Y")


def format_clock_time(value: time) -> str:
    """Return a time such as "7:30 AM"."""
    return dateformat.time_format(value, "g:i A")


def format_money(amount: Decimal) -> str:
    """Return a stated amount: "$2,450" when whole, "$62.50" otherwise."""
    if amount == amount.to_integral_value():
        return f"${amount:,.0f}"
    return f"${amount:,.2f}"


def format_whole_dollars(amount: Decimal) -> str:
    """Return an amount already rounded to whole dollars, such as "$2,492"."""
    return f"${amount:,.0f}"


def format_dollar_range(lowest: Decimal, highest: Decimal) -> str:
    """Return one amount when both agree, else the lowest and highest as a range."""
    if lowest == highest:
        return format_whole_dollars(lowest)
    return f"{format_whole_dollars(lowest)}{EN_DASH}{format_whole_dollars(highest)}"


def format_count(value: int | Decimal) -> str:
    """Return a count such as "12,345"."""
    return f"{value:,.0f}"


def format_percent(value: Decimal) -> str:
    """Return a percent such as "6.1%"."""
    return f"{value:.1f}%"


def format_plain_number(value: Decimal) -> str:
    """Return a number without trailing zeros or an exponent: "10", "9.5"."""
    return f"{value.normalize():f}"


def format_age_range(youngest: int, oldest: int | None) -> str:
    """Return an age group's limits in months, or its youngest age and older."""
    if oldest is None:
        return f"{youngest} months and older"
    return f"{youngest}{EN_DASH}{oldest} months"


def format_hours_per_day(hours: Decimal) -> str:
    if hours == 1:
        return "1 hour per day"
    return f"{format_plain_number(hours)} hours per day"


def format_days_per_week(days: int) -> str:
    if days == 1:
        return "1 day per week"
    return f"{days} days per week"


def format_month_count(months: int | Decimal) -> str:
    if months == 1:
        return "1 month"
    return f"{format_plain_number(Decimal(months))} months"


def format_radius(miles: Decimal) -> str:
    """Return a search radius such as "1.00 mi"."""
    return f"{miles:.2f} mi"


def format_recorded(contact: Contact) -> str:
    """Return how and when a value was recorded, such as "Call, Oct 14, 2026"."""
    return f"{contact.get_method_display()}, {format_short_date(contact.contact_date)}"


def format_closure(label_start: str, contact: Contact) -> str:
    """Return a closure label with its month and method, such as "(Oct 2026, call)"."""
    month = format_month_label(contact.contact_date)
    return f"{label_start} ({month}, {contact.get_method_display().lower()})"

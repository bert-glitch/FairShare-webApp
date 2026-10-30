"""Server-side input checks. Every form value is validated here before it reaches SQL."""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,50}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_AMOUNT = Decimal("99999999.99")


class ValidationError(ValueError):
    """A problem with what the user typed. The message is safe to show."""


def clean_text(value, field, max_len, required=True):
    value = (value or "").strip()
    if required and not value:
        raise ValidationError(f"{field} is required.")
    if len(value) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters.")
    return value


def clean_username(value):
    value = (value or "").strip()
    if not USERNAME_RE.match(value):
        raise ValidationError("Username must be 3-50 characters: letters, numbers, dots or underscores.")
    return value


def clean_email(value):
    value = (value or "").strip()
    if not value:
        return None                     # email is optional; store NULL, never an empty string
    if len(value) > 100 or not EMAIL_RE.match(value):
        raise ValidationError("Enter a valid email address, or leave it empty.")
    return value


def validate_password(password):
    if password is None or len(password) < 8:
        raise ValidationError("Password must be at least 8 characters.")
    if len(password) > 128:
        raise ValidationError("Password must be at most 128 characters.")
    return password


def parse_money(text, field="Amount", allow_zero=False):
    """Turn text like '1,250.50' into an exact Decimal with 2 places. Never uses floats."""
    raw = (text or "").strip().replace(",", "")
    if not raw:
        raise ValidationError(f"{field} is required.")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValidationError(f"{field} must be a number such as 150.00.")
    if not value.is_finite():
        raise ValidationError(f"{field} must be a number such as 150.00.")
    if value.as_tuple().exponent < -2:
        raise ValidationError(f"{field} can have at most 2 decimal places.")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValidationError(f"{field} must be greater than zero." if not allow_zero else f"{field} cannot be negative.")
    if value > MAX_AMOUNT:
        raise ValidationError(f"{field} is too large.")
    return value.quantize(Decimal("0.01"))


def parse_int(value, field="Value"):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"{field} is not valid.")


def parse_date(text, field="Date", required=True):
    text = (text or "").strip()
    if not text:
        if required:
            raise ValidationError(f"{field} is required.")
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"{field} must be a valid date (YYYY-MM-DD).")


def today():
    return date.today()

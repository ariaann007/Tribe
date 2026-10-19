from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def inr(value, decimals=2):
    """Format a number with Indian digit grouping: 2,90,750.00"""
    if value is None or value == "":
        return "—"
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    negative = number < 0
    text = f"{abs(number):.{int(decimals)}f}"
    whole, _, fraction = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    result = whole + ("." + fraction if fraction else "")
    return ("-" if negative else "") + result


@register.filter
def gbp(value, decimals=0):
    """Format a number with standard grouping: 35,000"""
    if value is None or value == "":
        return "—"
    try:
        return f"{Decimal(value):,.{int(decimals)}f}"
    except (InvalidOperation, TypeError, ValueError):
        return value


@register.filter
def money(value, currency):
    """₹2,90,750 for INR, £35,000 for GBP."""
    if value is None:
        return "—"
    return f"£{gbp(value)}" if currency == "GBP" else f"₹{inr(value, 0)}"


@register.filter
def days(value):
    """1.0 -> 1, 1.5 -> 1.5"""
    if value is None:
        return "—"
    number = Decimal(value).normalize()
    return f"{number:f}"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_thousand(n):
    words = []
    if n >= 100:
        words.append(f"{_ONES[n // 100]} Hundred")
        n %= 100
    if n >= 20:
        words.append(_TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        words.append(_ONES[n])
    return " ".join(words)


def indian_number_words(n):
    """1770082 -> 'Seventeen Lakh Seventy Thousand Eighty-Two' (Indian numbering)."""
    if n == 0:
        return "Zero"
    parts = []
    for size, label in ((10 ** 7, "Crore"), (10 ** 5, "Lakh"), (10 ** 3, "Thousand")):
        if n >= size:
            parts.append(f"{indian_number_words(n // size) if size == 10 ** 7 else _below_thousand(n // size)} {label}")
            n %= size
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


@register.filter
def inr_words(value):
    """17670.82 -> 'Rupees Seventeen Thousand Six Hundred Seventy and Eighty-Two Paise Only'"""
    try:
        amount = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return ""
    if amount < 0:
        return ""
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    words = f"Rupees {indian_number_words(rupees)}"
    if paise:
        words += f" and {_below_thousand(paise)} Paise"
    return words + " Only"

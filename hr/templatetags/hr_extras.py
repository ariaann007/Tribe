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
def days(value):
    """1.0 -> 1, 1.5 -> 1.5"""
    if value is None:
        return "—"
    number = Decimal(value).normalize()
    return f"{number:f}"

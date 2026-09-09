from __future__ import annotations


def mask(value: str) -> str:
    """Show enough of a secret to recognise it, never enough to use it."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"

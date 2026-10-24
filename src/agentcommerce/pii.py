"""PII scrubbing for training data, logs and error messages.

Card numbers are only treated as cards when they pass the Luhn checksum, so order numbers
and timestamps are left alone. SSNs follow the issuing rules (no 000/666/9xx area, no 00 group,
no 0000 serial). Everything that leaves the process (dataset rows, log lines, exception text)
goes through `scrub`.
"""

from __future__ import annotations

import logging
import re

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
SSN = re.compile(r"(?<!\d)(\d{3})-(\d{2})-(\d{4})(?!\d)")
PHONE = re.compile(r"(?<![\w-])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w-])")


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _card(m: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    if 13 <= len(digits) <= 19 and luhn_ok(digits):
        return "[CARD]"
    return m.group(0)


def _ssn(m: re.Match[str]) -> str:
    area, group, serial = m.groups()
    if area in {"000", "666"} or area.startswith("9") or group == "00" or serial == "0000":
        return m.group(0)
    return "[SSN]"


def scrub(text: str) -> str:
    """Mask cards (Luhn-valid), SSNs, emails and phone numbers."""
    text = CARD.sub(_card, str(text))
    text = SSN.sub(_ssn, text)
    text = EMAIL.sub("[EMAIL]", text)
    return PHONE.sub("[PHONE]", text)


def has_pii(text: str) -> bool:
    return scrub(text) != str(text)


class ScrubFilter(logging.Filter):
    """Logging filter: rewrites the final message so no PII reaches any handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(record.getMessage())
        record.args = None
        return True


def get_logger(name: str = "agentcommerce") -> logging.Logger:
    log = logging.getLogger(name)
    if not any(isinstance(f, ScrubFilter) for f in log.filters):
        log.addFilter(ScrubFilter())
    return log


class SafeError(ValueError):
    """ValueError whose message is scrubbed before it is stored."""

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))

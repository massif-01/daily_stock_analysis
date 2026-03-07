# -*- coding: utf-8 -*-
"""
Shared stock code utilities.
"""

from __future__ import annotations

import re
from typing import Optional


def is_code_like(value: str) -> bool:
    """Check if string looks like a stock code (5-6 digits or 1-5 letters)."""
    text = value.strip().upper()
    if not text:
        return False
    if text.isdigit() and len(text) in (5, 6):
        return True
    for suffix in (".SH", ".SZ", ".SS"):
        if text.endswith(suffix):
            base = text[: -len(suffix)].strip()
            if base.isdigit() and len(base) in (5, 6):
                return True
    if re.match(r"^[A-Z]{1,5}(\.[A-Z])?$", text):
        return True
    return False


def normalize_code(raw: str) -> Optional[str]:
    """Normalize and validate a single stock code."""
    text = raw.strip().upper()
    if not text:
        return None
    if text.isdigit() and len(text) in (5, 6):
        return text
    if re.match(r"^[A-Z]{1,5}(\.[A-Z])?$", text):
        return text
    for suffix in (".SH", ".SZ", ".SS"):
        if text.endswith(suffix):
            base = text[: -len(suffix)].strip()
            if base.isdigit() and len(base) in (5, 6):
                return base
    return None

"""Common helper utilities for formatting and validation."""

from __future__ import annotations

import re
from datetime import datetime, timezone


def format_number(value: float) -> str:
    """Format a number into a human-readable string (e.g. 1.23M, 45.2K)."""
    if value is None:
        return "N/A"
    abs_val = abs(value)
    sign = "-" if value < 0 else ""
    if abs_val >= 1_000_000_000:
        return f"{sign}{abs_val / 1_000_000_000:.2f}B"
    if abs_val >= 1_000_000:
        return f"{sign}{abs_val / 1_000_000:.2f}M"
    if abs_val >= 1_000:
        return f"{sign}{abs_val / 1_000:.1f}K"
    if abs_val >= 1:
        return f"{sign}{abs_val:.2f}"
    if abs_val >= 0.01:
        return f"{sign}{abs_val:.4f}"
    if abs_val == 0:
        return "0"
    return f"{sign}{abs_val:.8f}"


def format_price(value: float) -> str:
    """Format a price with appropriate decimal places."""
    if value >= 1:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:.4f}"
    if value >= 0.0001:
        return f"${value:.6f}"
    return f"${value:.10f}"


def format_percent(value: float) -> str:
    """Format a percentage with a directional arrow."""
    if value >= 0:
        return f"▲ {value:.1f}%"
    return f"▼ {abs(value):.1f}%"


def time_ago(dt: datetime) -> str:
    """Return a human-readable 'time ago' string."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def truncate(text: str, max_length: int = 20) -> str:
    """Truncate text to *max_length* characters."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"


def is_valid_evm_address(address: str) -> bool:
    """Check if *address* looks like a valid EVM (0x...) address."""
    return bool(re.match(r"^0x[0-9a-fA-F]{40}$", address))


def is_valid_solana_address(address: str) -> bool:
    """Check if *address* looks like a valid Solana base58 address."""
    return bool(re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address))


def escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    for ch in special:
        text = text.replace(ch, f"\\{ch}")
    return text

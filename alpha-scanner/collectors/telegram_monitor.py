"""Telegram group monitor for alpha calls — OPTIONAL, requires Telethon credentials."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from collectors.base import BaseCollector
from config.settings import settings

logger = logging.getLogger(__name__)

CASHTAG_RE = re.compile(r"[\$#]([A-Z]{2,10})\b")
EVM_ADDR_RE = re.compile(r"\b(0x[0-9a-fA-F]{40})\b")
SOL_ADDR_RE = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{40,44})\b")
ALPHA_KEYWORDS = {"buy", "ape", "send it", "gem", "call", "entry", "degen", "moon", "100x", "pump"}


class TelegramMonitorCollector(BaseCollector):
    """Monitor Telegram groups for token alpha calls using Telethon."""

    name = "telegram_monitor"

    def __init__(self, session: Any = None, rate_limiter: Any = None) -> None:
        # Session/rate_limiter not used for Telethon, but kept for interface consistency
        super().__init__(session, rate_limiter)  # type: ignore[arg-type]
        self._client = None
        self._signal_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._enabled = settings.telegram_monitoring_enabled

    async def start_listening(self) -> None:
        """Start the Telethon client and listen for messages."""
        if not self._enabled:
            logger.info("Telegram monitoring disabled (no API credentials)")
            return

        try:
            from telethon import TelegramClient, events  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("Telethon not installed, Telegram monitoring disabled")
            self._enabled = False
            return

        self._client = TelegramClient(
            "alpha_scanner_session",
            settings.TELEGRAM_API_ID,
            settings.TELEGRAM_API_HASH,
        )

        await self._client.start()
        logger.info("Telethon client started")

        groups = settings.TELEGRAM_ALPHA_GROUPS
        if not groups:
            logger.info("No Telegram alpha groups configured")
            return

        @self._client.on(events.NewMessage(chats=groups))
        async def handler(event: Any) -> None:
            text = event.raw_text or ""
            signals = self._parse_message(text, event)
            for signal in signals:
                await self._signal_queue.put(signal)

        logger.info("Listening to %d Telegram groups", len(groups))

    async def collect(self) -> list[dict[str, Any]]:
        """Drain accumulated signals from the queue."""
        if not self._enabled:
            return []

        signals: list[dict[str, Any]] = []
        while not self._signal_queue.empty():
            try:
                signals.append(self._signal_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if signals:
            logger.info("Telegram monitor drained %d signals", len(signals))
        return signals

    def _parse_message(self, text: str, event: Any) -> list[dict[str, Any]]:
        """Parse a Telegram message for token references."""
        signals: list[dict[str, Any]] = []
        text_lower = text.lower()

        # Check for alpha keywords
        keyword_count = sum(1 for kw in ALPHA_KEYWORDS if kw in text_lower)
        if keyword_count == 0:
            return []

        sentiment = min(keyword_count / 4, 1.0)

        cashtags = CASHTAG_RE.findall(text)
        evm_addrs = EVM_ADDR_RE.findall(text)
        sol_addrs = [a for a in SOL_ADDR_RE.findall(text) if len(a) >= 40]

        chat_name = ""
        try:
            chat_name = str(getattr(event.chat, "title", ""))
        except Exception:
            pass

        for symbol in set(cashtags):
            signals.append({
                "type": "social",
                "source": "telegram",
                "token_symbol": symbol,
                "contract_address": "",
                "mention_count": 1,
                "sentiment_score": sentiment,
                "velocity": 0,
                "metadata": {
                    "group": chat_name,
                    "text": text[:300],
                    "keyword_count": keyword_count,
                },
            })

        for addr in evm_addrs + sol_addrs:
            signals.append({
                "type": "social",
                "source": "telegram",
                "token_symbol": "",
                "contract_address": addr,
                "mention_count": 1,
                "sentiment_score": sentiment,
                "velocity": 0,
                "metadata": {"group": chat_name, "text": text[:300]},
            })

        return signals

    async def stop_listening(self) -> None:
        if self._client:
            await self._client.disconnect()
            logger.info("Telethon client disconnected")

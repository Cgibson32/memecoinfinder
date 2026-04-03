"""Narrative/theme alignment scoring — boost tokens riding trending narratives."""

from __future__ import annotations

import logging
import re
from typing import Any

from storage.database import Database

logger = logging.getLogger(__name__)

# Seed narratives with common crypto themes
SEED_NARRATIVES: list[dict[str, Any]] = [
    {"name": "AI", "keywords": ["ai", "gpt", "neural", "brain", "artificial", "intelligence", "agent", "llm", "openai"]},
    {"name": "Political", "keywords": ["trump", "biden", "maga", "election", "president", "political", "vote", "congress"]},
    {"name": "Dog/Cat Meme", "keywords": ["dog", "doge", "shiba", "inu", "wif", "cat", "kitty", "meow", "pup", "bonk"]},
    {"name": "Frog Meme", "keywords": ["pepe", "frog", "kek", "ribbit", "pond"]},
    {"name": "Gaming", "keywords": ["game", "gaming", "play", "quest", "pixel", "arcade", "metaverse"]},
    {"name": "DeFi", "keywords": ["defi", "swap", "yield", "stake", "lending", "liquidity", "amm"]},
    {"name": "RWA", "keywords": ["rwa", "real world", "tokenize", "asset", "property"]},
    {"name": "Layer2", "keywords": ["l2", "layer2", "rollup", "zk", "optimistic"]},
    {"name": "Culture", "keywords": ["meme", "viral", "trend", "tiktok", "internet", "based", "chad"]},
]


class NarrativeAnalyzer:
    """Detect narrative alignment and score tokens by trending theme match."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._initialized = False

    async def initialize(self) -> None:
        """Seed initial narratives if not already in DB."""
        if self._initialized:
            return
        for narr in SEED_NARRATIVES:
            await self.db.upsert_narrative(
                name=narr["name"],
                keywords=narr["keywords"],
                heat_score=0.5,
                source="seed",
            )
        self._initialized = True

    async def analyze(self, token_name: str, token_symbol: str) -> dict[str, Any]:
        """Score token based on narrative alignment."""
        await self.initialize()

        narratives = await self.db.get_active_narratives(min_heat=0.1)
        if not narratives:
            return {"narrative_score": 0, "matched_narratives": [], "strongest_match": ""}

        token_text = f"{token_name} {token_symbol}".lower()
        matches: list[dict[str, Any]] = []

        for narr in narratives:
            keywords = narr["keywords"]
            match_count = 0
            for kw in keywords:
                if re.search(r"\b" + re.escape(kw) + r"\b", token_text):
                    match_count += 1

            if match_count > 0:
                strength = min(match_count / 3, 1.0)
                matches.append({
                    "narrative": narr["name"],
                    "match_count": match_count,
                    "strength": strength,
                    "heat": narr["heat_score"],
                })

        if not matches:
            return {"narrative_score": 0, "matched_narratives": [], "strongest_match": ""}

        # Score based on strongest match + narrative heat
        best = max(matches, key=lambda m: m["strength"] * m["heat"])
        score = best["strength"] * best["heat"] * 100

        return {
            "narrative_score": min(score, 100),
            "matched_narratives": [m["narrative"] for m in matches],
            "strongest_match": best["narrative"],
            "match_details": matches,
        }

    async def update_narrative_heat(
        self, narrative_name: str, heat_delta: float
    ) -> None:
        """Adjust heat score for a narrative based on social signals."""
        narratives = await self.db.get_active_narratives(min_heat=0)
        for narr in narratives:
            if narr["name"] == narrative_name:
                new_heat = max(min(narr["heat_score"] + heat_delta, 2.0), 0)
                await self.db.upsert_narrative(
                    name=narrative_name,
                    keywords=narr["keywords"],
                    heat_score=new_heat,
                    source=narr.get("source", ""),
                )
                break

    async def detect_narratives_from_social(
        self, trending_terms: list[str]
    ) -> None:
        """Update narrative heat based on social trend data."""
        narratives = await self.db.get_active_narratives(min_heat=0)
        for narr in narratives:
            for term in trending_terms:
                for kw in narr["keywords"]:
                    if kw in term.lower():
                        await self.update_narrative_heat(narr["name"], 0.1)
                        break

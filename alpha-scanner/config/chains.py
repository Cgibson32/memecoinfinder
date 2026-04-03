"""Chain-specific configuration: API identifiers, explorers, and network mappings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChainConfig:
    name: str
    dexscreener_id: str
    geckoterminal_id: str
    goplus_chain_id: str
    explorer_base_url: str
    explorer_api_url: str
    explorer_token_path: str
    birdeye_base: str


CHAIN_CONFIG: dict[str, ChainConfig] = {
    "solana": ChainConfig(
        name="Solana",
        dexscreener_id="solana",
        geckoterminal_id="solana",
        goplus_chain_id="solana",
        explorer_base_url="https://solscan.io",
        explorer_api_url="https://public-api.solscan.io",
        explorer_token_path="/token/",
        birdeye_base="https://birdeye.so/token",
    ),
    "ethereum": ChainConfig(
        name="Ethereum",
        dexscreener_id="ethereum",
        geckoterminal_id="eth",
        goplus_chain_id="1",
        explorer_base_url="https://etherscan.io",
        explorer_api_url="https://api.etherscan.io/api",
        explorer_token_path="/token/",
        birdeye_base="https://birdeye.so/token",
    ),
    "base": ChainConfig(
        name="Base",
        dexscreener_id="base",
        geckoterminal_id="base",
        goplus_chain_id="8453",
        explorer_base_url="https://basescan.org",
        explorer_api_url="https://api.basescan.org/api",
        explorer_token_path="/token/",
        birdeye_base="https://birdeye.so/token",
    ),
    "bsc": ChainConfig(
        name="BSC",
        dexscreener_id="bsc",
        geckoterminal_id="bsc",
        goplus_chain_id="56",
        explorer_base_url="https://bscscan.com",
        explorer_api_url="https://api.bscscan.com/api",
        explorer_token_path="/token/",
        birdeye_base="https://birdeye.so/token",
    ),
}


def get_chain_config(chain: str) -> ChainConfig | None:
    """Return the ChainConfig for *chain* or None if unsupported."""
    return CHAIN_CONFIG.get(chain.lower())

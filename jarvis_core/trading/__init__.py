"""Deterministic Binance paper-trading engine for Codex Jarvis.

The package is deliberately separated from the conversational voice brain.
Market data and trade decisions are reproducible code; the AI may explain
results but cannot bypass the risk engine.
"""
from .config import TradingConfig, load_config
from .engine import TradingEngine

__all__ = ["TradingConfig", "TradingEngine", "load_config"]

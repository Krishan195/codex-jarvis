"""Trading configuration with explicit paper-mode risk limits."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import os
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "codex-jarvis"
CONFIG_PATH = CONFIG_DIR / "trading.json"


@dataclass
class StrategyConfig:
    version: str = "trend-pullback-v1"
    signal_interval: str = "15m"
    context_intervals: list[str] = field(default_factory=lambda: ["1h", "4h"])
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    long_rsi_min: float = 52.0
    long_rsi_max: float = 68.0
    short_rsi_min: float = 32.0
    short_rsi_max: float = 48.0
    atr_period: int = 14
    min_atr_percent: float = 0.25
    max_atr_percent: float = 2.50
    min_volume_ratio: float = 1.00
    max_spread_bps: float = 5.0
    max_adverse_book_imbalance: float = 0.25
    stop_atr_buffer: float = 0.50
    reward_risk: float = 2.0
    signal_expiry_minutes: int = 15


@dataclass
class CostConfig:
    taker_fee_bps: float = 5.0
    slippage_bps: float = 2.0
    include_one_funding_interval: bool = True


@dataclass
class RiskConfig:
    allocated_capital_quote: float = 0.0
    max_leverage: float = 2.0
    risk_per_trade_percent: float = 0.50
    max_aggregate_exposure_percent: float = 50.0
    max_simultaneous_positions: int = 2
    daily_loss_limit_percent: float = 2.0
    max_drawdown_percent: float = 10.0


@dataclass
class ManualTradingConfig:
    # Independent of the strategy's leverage setting. Explicit local opt-in.
    # Defaults are usable with BTC/ETH Demo minimum notionals while remaining opt-in.
    enabled: bool = False
    max_leverage: int = 10
    max_margin_quote: float = 50.0


@dataclass
class TradingConfig:
    schema_version: int = 1
    mode: str = "PAPER"
    enabled: bool = False
    paused: bool = False
    market_type: str = "futures"
    margin_mode: str = "ISOLATED"
    permitted_symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    manual: ManualTradingConfig = field(default_factory=ManualTradingConfig)
    stale_grace_seconds: int = 120
    paper_loop_seconds: int = 60

    def validate(self) -> None:
        import math
        if not 1 <= self.manual.max_leverage <= 125 or int(self.manual.max_leverage) != self.manual.max_leverage:
            raise ValueError("manual.max_leverage must be an integer from 1 to 125")
        if not math.isfinite(self.manual.max_margin_quote) or self.manual.max_margin_quote <= 0:
            raise ValueError("manual.max_margin_quote must be finite and positive")
        if self.mode != "PAPER":
            raise ValueError("This build enables PAPER mode only.")
        if self.market_type not in {"spot", "futures"}:
            raise ValueError("market_type must be spot or futures")
        if self.market_type == "futures" and self.margin_mode not in {"ISOLATED", "CROSSED"}:
            raise ValueError("margin_mode must be ISOLATED or CROSSED for futures")
        if not self.permitted_symbols:
            raise ValueError("At least one permitted symbol is required.")
        if len(set(self.permitted_symbols)) != len(self.permitted_symbols):
            raise ValueError("permitted_symbols contains duplicates.")
        if self.risk.allocated_capital_quote <= 0:
            raise ValueError(
                "allocated_capital_quote must be explicitly configured before paper trading."
            )
        if not 0 < self.risk.risk_per_trade_percent <= 100:
            raise ValueError("risk_per_trade_percent must be in (0, 100].")
        if self.risk.max_leverage < 1:
            raise ValueError("max_leverage must be >= 1.")
        if self.risk.max_simultaneous_positions < 1:
            raise ValueError("max_simultaneous_positions must be >= 1.")
        if self.strategy.signal_interval != "15m":
            raise ValueError("Baseline v1 currently supports 15m signals.")
        if self.strategy.context_intervals != ["1h", "4h"]:
            raise ValueError("Baseline v1 currently expects 1h and 4h context.")
        if self.strategy.reward_risk <= 0:
            raise ValueError("reward_risk must be positive.")


def _construct(data: dict[str, Any]) -> TradingConfig:
    return TradingConfig(
        schema_version=int(data.get("schema_version", 1)),
        mode=str(data.get("mode", "PAPER")).upper(),
        enabled=bool(data.get("enabled", False)),
        paused=bool(data.get("paused", False)),
        market_type=str(data.get("market_type", "futures")).lower(),
        margin_mode=str(data.get("margin_mode", "ISOLATED")).upper(),
        permitted_symbols=[
            str(x).upper() for x in data.get("permitted_symbols", ["BTCUSDT", "ETHUSDT"])
        ],
        strategy=StrategyConfig(**data.get("strategy", {})),
        costs=CostConfig(**data.get("costs", {})),
        risk=RiskConfig(**data.get("risk", {})),
        manual=ManualTradingConfig(**data.get("manual", {})),
        stale_grace_seconds=int(data.get("stale_grace_seconds", 120)),
        paper_loop_seconds=max(15, int(data.get("paper_loop_seconds", 60))),
    )


def load_config(path: Path | None = None, *, require_enabled: bool = False) -> TradingConfig:
    path = path or CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Trading config not found: {path}. Run jarvis-trader init --capital AMOUNT first."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    cfg = _construct(data)
    cfg.validate()
    if require_enabled and not cfg.enabled:
        raise ValueError("Paper trading is disabled in configuration.")
    return cfg


def write_config(cfg: TradingConfig, path: Path | None = None) -> Path:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    return path


def initialize_paper_config(
    capital: float,
    *,
    market_type: str = "futures",
    path: Path | None = None,
) -> Path:
    if capital <= 0:
        raise ValueError("Paper capital must be positive.")
    cfg = TradingConfig(
        enabled=False,
        market_type=market_type.lower(),
        risk=RiskConfig(allocated_capital_quote=float(capital)),
    )
    cfg.validate()
    return write_config(cfg, path)

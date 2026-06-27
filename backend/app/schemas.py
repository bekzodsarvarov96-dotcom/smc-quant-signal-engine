"""Pydantic-схемы API."""
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    symbol: str
    timeframe: str
    direction: str
    score: float
    grade: str
    probability: float
    confirmations: int
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    reasons: dict
    module_scores: dict
    status: str


class TradeCreate(BaseModel):
    signal_id: int | None = None
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float | None = None
    qty: float = 0.0
    notes: str | None = None


class TradeClose(BaseModel):
    exit_price: float
    notes: str | None = None


class TradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    signal_id: int | None
    symbol: str
    direction: str
    entry_price: float
    exit_price: float | None
    stop_loss: float
    take_profit: float | None
    qty: float
    pnl: float | None
    pnl_r: float | None
    opened_at: datetime
    closed_at: datetime | None
    is_open: bool
    notes: str | None


class StatsOut(BaseModel):
    total_trades: int
    open_trades: int
    closed_trades: int
    wins: int
    losses: int
    winrate: float
    total_pnl: float
    total_r: float
    avg_r: float
    best_r: float
    worst_r: float
    profit_factor: float


class BacktestRequest(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "15m"
    days: int = 30
    min_score: int | None = None
    min_confirmations: int | None = None
    allowed_grades: list[str] | None = None


class BacktestRunRequest(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    months: int = 24


class BacktestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    total_signals: int
    wins: int
    losses: int
    winrate: float
    total_r: float
    avg_r: float
    max_drawdown_r: float
    profit_factor: float
    avg_rr: float
    expected_value: float
    params: dict
    trades: list

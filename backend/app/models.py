"""ORM-модели: сигналы, сделки журнала, результаты бэктестов."""
import enum
from datetime import datetime

from sqlalchemy import String, Float, Integer, DateTime, Enum, JSON, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Direction(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    TP1 = "TP1_HIT"
    TP2 = "TP2_HIT"
    TP3 = "TP3_HIT"
    STOPPED = "STOPPED"
    EXPIRED = "EXPIRED"


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    direction: Mapped[Direction] = mapped_column(Enum(Direction))

    score: Mapped[float] = mapped_column(Float)          # 0..100
    grade: Mapped[str] = mapped_column(String(3), default="B", index=True)  # A+..D
    probability: Mapped[float] = mapped_column(Float)    # 0..1
    confirmations: Mapped[int] = mapped_column(Integer)

    entry: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    tp1: Mapped[float] = mapped_column(Float)
    tp2: Mapped[float] = mapped_column(Float)
    tp3: Mapped[float] = mapped_column(Float)

    reasons: Mapped[dict] = mapped_column(JSON)          # {module: [reasons]}
    module_scores: Mapped[dict] = mapped_column(JSON)    # {module: score}
    entry_timing: Mapped[dict] = mapped_column(JSON, default=dict)  # Этап 1: метрики тайминга входа
    setup_signature: Mapped[dict] = mapped_column(JSON, default=dict)  # Этап 2: {sweep, bos} для duplicate-фильтра
    energy_score: Mapped[float] = mapped_column(Float, default=0.0)      # ДИАГНОСТИКА (не влияет на сигнал)
    pressure_score: Mapped[float] = mapped_column(Float, default=0.0)    # ДИАГНОСТИКА
    late_risk: Mapped[float] = mapped_column(Float, default=0.0)         # ДИАГНОСТИКА
    entry_verdict: Mapped[str] = mapped_column(String(16), default="")   # EARLY/GOOD/LATE/VERY LATE
    filter_diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)  # ДИАГНОСТИКА 6 фильтров (не влияет на сигнал)

    status: Mapped[SignalStatus] = mapped_column(Enum(SignalStatus), default=SignalStatus.ACTIVE)
    telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False)


class Trade(Base):
    """Журнал сделок (ручной или авто-трекинг сигналов)."""
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[Direction] = mapped_column(Enum(Direction))

    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)

    qty: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_r: Mapped[float | None] = mapped_column(Float, nullable=True)  # PnL в R

    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    symbol: Mapped[str] = mapped_column(String(20))
    timeframe: Mapped[str] = mapped_column(String(8))
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)

    total_signals: Mapped[int] = mapped_column(Integer)
    wins: Mapped[int] = mapped_column(Integer)
    losses: Mapped[int] = mapped_column(Integer)
    winrate: Mapped[float] = mapped_column(Float)
    total_r: Mapped[float] = mapped_column(Float)
    avg_r: Mapped[float] = mapped_column(Float)
    max_drawdown_r: Mapped[float] = mapped_column(Float)
    profit_factor: Mapped[float] = mapped_column(Float)
    avg_rr: Mapped[float] = mapped_column(Float, default=0.0)        # средний фактический RR победителей
    expected_value: Mapped[float] = mapped_column(Float, default=0.0) # EV в R на сделку
    params: Mapped[dict] = mapped_column(JSON)
    trades: Mapped[list] = mapped_column(JSON)  # компактный список сделок


class ForwardTestResult(Base):
    """Журнал forward-теста: автоматически закрытый исход каждого сигнала.

    Исход считается ТОЙ ЖЕ математикой, что и бэктест (portfolio.py):
    частичные TP 50/30/20, безубыток после TP1, приоритет SL в одном баре,
    таймаут 96 баров, издержки taker+slippage(+funding) — для честного
    сравнения forward vs backtest.
    """
    __tablename__ = "forward_test_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, index=True, unique=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    grade: Mapped[str] = mapped_column(String(3))
    score: Mapped[float] = mapped_column(Float)

    entry: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    tp1: Mapped[float] = mapped_column(Float)
    tp2: Mapped[float] = mapped_column(Float)
    tp3: Mapped[float] = mapped_column(Float)

    opened_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime)
    holding_minutes: Mapped[int] = mapped_column(Integer)
    bars_held: Mapped[int] = mapped_column(Integer)

    result: Mapped[str] = mapped_column(String(10), index=True)   # WIN | LOSS | BREAKEVEN
    final_r: Mapped[float] = mapped_column(Float)                  # нетто, в R
    exit_reason: Mapped[str] = mapped_column(String(16))           # SL | TP_CASCADE | TIMEOUT
    tp_hits: Mapped[dict] = mapped_column(JSON, default=dict)      # {"tp1":bool,...}
    fees_r: Mapped[float] = mapped_column(Float, default=0.0)
    funding_r: Mapped[float] = mapped_column(Float, default=0.0)
    entry_timing: Mapped[dict] = mapped_column(JSON, default=dict)  # Этап 1: метрики тайминга входа
    entry_quality: Mapped[dict] = mapped_column(JSON, default=dict)  # ДИАГНОСТИКА: energy/pressure/late_risk/verdict
    active_modules: Mapped[list] = mapped_column(JSON, default=list)  # АТРИБУЦИЯ: модули, участвовавшие в сигнале
    filter_diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)  # ДИАГНОСТИКА 6 фильтров (для анализа качества)

"""Конфигурация приложения (pydantic-settings, переменные из .env)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Реальные креды берутся из .env (см. .env.example). Это значение — заглушка.
    database_url: str = "postgresql+asyncpg://USER:PASSWORD@localhost:5432/crypto_signals_top20"

    binance_base_url: str = "https://fapi.binance.com"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    scan_symbols: str = "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,LINKUSDT,AVAXUSDT,XLMUSDT,TRXUSDT,LTCUSDT,BCHUSDT,DOTUSDT,NEARUSDT,APTUSDT,ATOMUSDT,INJUSDT,SEIUSDT,SUIUSDT"
    scan_timeframe: str = "1h"   # forward-test: валидированный ТФ
    htf_timeframe: str = "4h"
    scan_interval_seconds: int = 60

    # Отключение модулей анализа без изменения кода, через запятую:
    # DISABLED_MODULES=fibonacci,liquidation_clusters
    disabled_modules: str = ""

    min_signal_score: int = 30   # forward-test: параметр исследования
    min_confirmations: int = 3           # семейств; параметр исследования
    min_families: int = 3                 # минимум подтверждённых СЕМЕЙСТВ факторов
    allowed_grades: str = ""              # пусто = грейд-фильтр ОТКЛЮЧЁН (forward-test)
    block_counter_htf: bool = True        # запрет входов против старшего тренда

    # Автобэктест: ОТКЛЮЧЁН по умолчанию — тяжёлый процесс запускается
    # вручную через POST /api/v1/backtest/run или включается явно.
    enable_auto_backtest: bool = False        # env: ENABLE_AUTO_BACKTEST
    auto_backtest_enabled: bool | None = None # legacy-алиас, если задан — имеет приоритет
    auto_backtest_months: int = 24
    auto_backtest_timeframe: str = "1h"   # 24 мес на 15m = ~70k свечей/символ; 1h разумнее
    auto_backtest_interval_hours: int = 24

    # Издержки (Binance USDT-M Futures)
    taker_fee: float = 0.0005             # 0.05% за сторону
    slippage_bps: float = 2.0             # проскальзывание, б.п. за сторону
    include_funding_costs: bool = True

    # Риск-параметры
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5
    tp_r_multiples: tuple[float, float, float] = (1.0, 2.0, 3.0)

    @property
    def auto_backtest_on(self) -> bool:
        """Итоговый флаг: legacy-переменная (если задана) > новая > False."""
        if self.auto_backtest_enabled is not None:
            return self.auto_backtest_enabled
        return self.enable_auto_backtest

    @property
    def disabled_module_set(self) -> set[str]:
        return {m.strip() for m in self.disabled_modules.split(",") if m.strip()}

    @property
    def grades(self) -> list[str]:
        return [g.strip() for g in self.allowed_grades.split(",") if g.strip()]

    @property
    def symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.scan_symbols.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

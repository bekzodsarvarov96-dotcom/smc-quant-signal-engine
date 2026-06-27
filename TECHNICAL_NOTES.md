# Crypto Signals — SMC-сканер сигналов Binance Futures

Полноценное приложение для поиска сигналов **LONG / SHORT** на Binance USDT-M Futures:
FastAPI + PostgreSQL backend с модульным движком анализа, Telegram-уведомлениями,
бэктестингом и журналом сделок; Flutter-клиент с экранами сигналов, журнала и бэктеста.

## Архитектура

```
backend/
  app/
    main.py                  # FastAPI, lifespan: init БД + запуск сканера
    config.py                # настройки (.env, pydantic-settings)
    database.py              # async SQLAlchemy + PostgreSQL (asyncpg)
    models.py                # Signal, Trade, BacktestRun
    schemas.py               # Pydantic-схемы API
    binance/
      client.py              # REST: klines, OI history, funding, mark price
      streams.py             # WebSocket kline-стрим (авто-reconnect)
    analysis/                # МОДУЛЬНАЯ система анализа
      base.py                # AnalysisModule, MarketContext, ModuleResult, утилиты
      registry.py            # реестр модулей — точка расширения
      mtf.py                 # Multi Timeframe Analysis (htf_trend + жёсткий фильтр)
      market_structure.py    # HH/HL/LH/LL + BOS + CHOCH
      mss.py                 # Market Structure Shift (sweep → displacement → слом)
      liquidity.py           # Equal Highs/Lows + Liquidity Sweep Detection (расшир.)
      order_blocks.py        # Order Blocks + Strength Ranking (импульс/объём/свежесть)
      fvg.py                 # Fair Value Gap + Ranking (размер/свежесть/заполненность)
      fibonacci.py           # Fib 0.5 / 0.618 / 0.705 / 0.786
      ema.py                 # EMA50/EMA200 + HTF-подтверждение
      volume.py              # объём + дельта тейкеров
      volume_profile.py      # Volume Profile: POC / VAH / VAL
      vwap.py                # VWAP + полосы σ
      cvd.py                 # Cumulative Volume Delta + дивергенции/абсорбция
      open_interest.py       # матрица цена/OI
      oi_delta.py            # Open Interest Delta (скорость/ускорение OI)
      funding.py             # Funding Rate Extremes (абсолютные + перцентильные)
      liquidation.py         # Liquidation Cluster Detection (оценочная модель)
      rsi_divergence.py      # бычьи/медвежьи дивергенции RSI
    engine/
      scoring.py             # скоринг 0–100, конфирмации, ATR SL, TP1/2/3
      scanner.py             # фоновый цикл: скан → БД → Telegram → трекинг TP/SL
    notifications/telegram.py
    backtest/
      backtester.py          # walk-forward бэктест: WR, PF, Avg RR, Max DD, EV
      auto_backtest.py       # автобэктест 24 мес по всем символам (раз в сутки)
    api/routes.py            # REST API
flutter_app/                 # мобильный клиент (Material 3, тёмная тема)
```

## Как добавить новый алгоритм анализа

1. Создайте файл в `backend/app/analysis/`, унаследуйтесь от `AnalysisModule`:

```python
from .base import AnalysisModule, MarketContext, ModuleResult

class MyModule(AnalysisModule):
    name = "my_algo"
    weight = 1.0

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        # ctx.df — закрытые свечи, ctx.htf_df, ctx.open_interest, ctx.funding
        return ModuleResult(self.name, bias=1.0, score=0.7, reasons=["..."])
```

2. Добавьте экземпляр в `ALL_MODULES` в `registry.py`. Всё: движок, сканер,
   бэктестер и API подхватят модуль автоматически.

## Statistical Truth v1

Статистический пайплайн доказательства/опровержения преимущества:

* **score** нормируется по активным модулям: `|alignment| · √(participation/0.45) · 100`,
  где alignment — согласованность активных модулей, participation — доля «проснувшегося» веса;
* **подтверждения по семействам факторов** (trend / liquidity / flow / sentiment / momentum,
  см. `app/engine/families.py`) — минимум 3 различных семейства вместо подсчёта модулей;
* **грейды по эмпирическим перцентилям** фактического распределения score
  (A+ ≥ p97, A ≥ p90, B ≥ p75, C ≥ p50) — `app/engine/calibration.json`,
  обновляется фазой calibrate;
* **бэктест с реальными издержками**: taker-комиссии 0.05%/сторона, slippage 2 б.п./сторона,
  funding каждые 8ч (исторический ряд), перекрывающиеся позиции, риск-сайзинг % equity,
  mark-to-market equity-кривая → истинный Max Drawdown и Sharpe;
* **Walk-Forward** (6 фолдов, grid-search порогов на train, тест на следующем сегменте),
  **Monte Carlo** (1000 бутстреп-путей, p-value для E[R]>0), **IS/OOS** разделение.

Запуск полного аудита на реальных данных:
```bash
cd backend
python -m scripts.statistical_truth --symbols BTCUSDT,ETHUSDT,SOLUSDT --months 24
# отрицательный контроль пайплайна на случайных данных:
python -m scripts.statistical_truth --synthetic
```
Отчёт `STATISTICAL_TRUTH_REPORT.md` содержит метрики IS/OOS (Win Rate, Profit Factor,
Expectancy, Sharpe, Max DD, Average RR, число сделок), Monte-Carlo интервалы и
формальный вердикт о наличии статистического преимущества.

## Грейды A+ … D и жёсткие фильтры

| Грейд | Условия |
|---|---|
| A+ | score ≥ p97 распределения, ≥ 4 семейства, старший тренд строго по сигналу |
| A  | score ≥ p90, ≥ 4 семейства |
| B  | score ≥ p75, ≥ 3 семейства |
| C  | score ≥ p50 |
| D  | остальное |

Границы p50/p75/p90/p97 — эмпирические, из `calibration.json`.

Публикуются (и отправляются в Telegram) только грейды из `ALLOWED_GRADES`
(по умолчанию **A+ и A**). Жёсткие фильтры:
* `BLOCK_COUNTER_HTF=true` — входы против тренда старшего ТФ запрещены;
* `MIN_CONFIRMATIONS=3` — минимум 3 различных СЕМЕЙСТВА факторов в сторону сигнала.

## Система оценки 0–100

* Каждый модуль возвращает `bias ∈ [-1;+1]` (направление) и `score ∈ [0;1]` (уверенность).
* Итог = нормированная взвешенная сумма + бонус за каждое подтверждение (+4)
  и штраф за сильное противоречие (−6).
* Сигнал публикуется только при `score ≥ MIN_SIGNAL_SCORE` (по умолчанию 65)
  **и** `confirmations ≥ MIN_CONFIRMATIONS` (по умолчанию 4).
* Риск: стоп = ATR×1.5 **или** за ближайшим swing-уровнем (берётся консервативнее);
  TP1/TP2/TP3 = 1R/2R/3R.

## Запуск backend

```bash
cd backend
cp .env.example .env          # заполните Telegram-токен и chat_id
docker compose up --build     # PostgreSQL + API на :8000
# или локально:
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Swagger: http://localhost:8000/docs

### Ключевые эндпоинты

| Метод | Путь | Описание |
|---|---|---|
| GET | /api/v1/signals | Лента сигналов (фильтры symbol, status) |
| GET | /api/v1/analyze/{symbol} | Live-анализ символа по запросу |
| GET | /api/v1/modules | Список активных модулей и весов |
| POST | /api/v1/trades | Добавить сделку в журнал |
| POST | /api/v1/trades/{id}/close | Закрыть сделку (PnL и R считаются автоматически) |
| GET | /api/v1/stats | Winrate, Σ R, profit factor и пр. |
| POST | /api/v1/backtest | Запуск бэктеста на истории Binance |

## Telegram

Создайте бота через @BotFather, добавьте его в канал/чат, пропишите
`TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` в `.env`. Уведомления приходят
при появлении сигнала и при достижении TP1/TP2/TP3/SL.

## Запуск Flutter-клиента

```bash
cd flutter_app
flutter pub get
flutter run --dart-define=API_URL=http://<IP-машины-с-backend>:8000
# для эмулятора Android значение по умолчанию http://10.0.2.2:8000 уже подходит
```

## Forward-test (30 дней, без денег)

Конфигурация по умолчанию переведена на валидированные параметры исследования:
`SCAN_TIMEFRAME=1h`, `MIN_SIGNAL_SCORE=30`, `MIN_CONFIRMATIONS=3`,
`ALLOWED_GRADES=` (пусто — обязательный фильтр A/A+ отключён; грейд продолжает
записываться в каждый сигнал для последующего анализа).

Исход каждого сигнала закрывается автоматически **по закрытым свечам** его ТФ
той же математикой, что и бэктест (частичные TP 50/30/20, безубыток после TP1,
приоритет SL в баре, таймаут 96 баров, издержки taker+slippage+funding) и
записывается в `forward_test_results`: результат WIN/LOSS/BREAKEVEN, итоговый R,
время удержания, причина выхода, комиссии/funding в R.

* `GET /api/v1/forward-test/stats` — сигналы/завершённые, winrate, expectancy,
  PF, средний R, разбивка по символам и исходам, предупреждение при n<30.
* `GET /api/v1/forward-test/report` — Forward vs последний Backtest по
  WR/PF/Expectancy/AvgR с дельтами и вердиктом
  CONSISTENT / DIVERGENT / INSUFFICIENT_DATA.

## Attribution Analysis

Поиск набора модулей с максимальным Expectancy/PF на Out-of-Sample:

* `GET /api/v1/module-performance?symbol=&runs=5` — по сделкам последних
  бэктестов для каждого модуля: total_signals, winrate, average_r, expectancy,
  profit_factor и **contribution_score** = expectancy(сделки с активным
  модулем) − expectancy(без него), в R на сделку.
* `GET /api/v1/module-combinations?symbol=BTCUSDT&timeframe=1h` —
  **leave-one-out** по всем 20 модулям (честный ре-скоринг кандидатов без
  модуля по сырым bias/score + полная симуляция с издержками) с классификацией
  improves_pf / worsens_pf / statistically_useless / insufficient_data,
  плюс **greedy backward**-оптимизация: подмножество выбирается ТОЛЬКО на
  In-Sample (70%), результат оценивается на Out-of-Sample (30%).
  Требует кэшированный датасет — сначала `POST /api/v1/backtest/run`.
* `DISABLED_MODULES=fibonacci,liquidation_clusters` в `.env` — отключение
  модулей без изменения кода (имена — `GET /api/v1/modules`).
  ⚠️ Отключение `mtf_trend` деактивирует жёсткий HTF-фильтр.

Полный перебор 2^20 комбинаций невозможен; LOO + greedy — стандартная
аппроксимация. Ответ greedy содержит предупреждения о статистической
незначимости при малых выборках (риск подгонки под шум — каждая итерация
поиска добавляет степень свободы).

## Управление бэктестом и статус системы

Сканер сигналов и тяжёлый бэктест разделены. По умолчанию
`ENABLE_AUTO_BACKTEST=false` — при старте приложения автобэктест НЕ
запускается и фоновые задачи не создаются; API остаётся отзывчивым.

Ручной запуск бэктеста (фоновая задача, мгновенный ответ с task_id):
```bash
curl -X POST localhost:8000/api/v1/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","timeframe":"1h","months":24}'
# → {"task_id":"...","status":"queued","queue_position":1}
```
Очередь: один тяжёлый бэктест за раз (семафор), остальные ждут.
Статусы задач: `GET /api/v1/backtest/tasks`.

Мониторинг сервиса: `GET /api/v1/system/status` — scanner_running,
auto_backtest_running, active_tasks, memory_usage (процесс/система),
cpu_usage, uptime.

## Автоматический бэктест (24 месяца)

Если включён (`ENABLE_AUTO_BACKTEST=true`), при старте API и далее раз в `AUTO_BACKTEST_INTERVAL_HOURS` (24ч) система
прогоняет каждый сканируемый символ на последних `AUTO_BACKTEST_MONTHS` (24)
месяцах истории Binance на таймфрейме `AUTO_BACKTEST_TIMEFRAME` (1h) и
сохраняет в БД метрики: **Win Rate, Profit Factor, Average RR, Max Drawdown,
Expected Value (R/сделка)**. Результаты: `GET /api/v1/backtest`.

## Бэктестинг

Walk-forward на исторических klines Binance: на каждом баре движок видит
только прошлое; исход симулируется по high/low будущих баров. Частичная
фиксация 50/30/20% на TP1/TP2/TP3, перенос стопа в безубыток после TP1,
консервативное правило «SL приоритетнее TP в одном баре». Метрики:
winrate, суммарный и средний R, profit factor, max drawdown.

> Важно: OI и funding недоступны поисторически в нужной гранулярности,
> поэтому в бэктесте эти модули нейтральны — live-результаты могут отличаться.

## Дисклеймер

Приложение — аналитический инструмент. Сигналы не являются финансовой
рекомендацией; торговля фьючерсами с плечом сопряжена с высоким риском.

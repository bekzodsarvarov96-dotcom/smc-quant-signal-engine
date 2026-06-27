# SMC Quant Signal Engine

> Research-grade crypto signal system — SMC analysis on Binance USDT-M Futures
> with rigorous statistical validation, forward-testing and diagnostic analytics.
>
> Исследовательская сигнальная система для крипторынка — SMC-анализ фьючерсов
> Binance USDT-M со строгой статистической валидацией, форвард-тестом и диагностической аналитикой.

**Stack:** Python · FastAPI · PostgreSQL · SQLAlchemy (async) · pandas/numpy · matplotlib · Telegram Bot API · Docker

---

## 🇬🇧 English

### What it is
A production-grade backend that analyzes Binance USDT-M Futures using **Smart Money Concepts (SMC)** and classic indicators, scores each setup 0–100, and validates its own performance through forward-testing — not marketing claims. Built as a disciplined quant-research pipeline, not a "get-rich-quick" bot.

### Key engineering highlights
- **20 analysis modules across 5 signal families** (structure, liquidity, flow, sentiment, momentum): market structure, BOS/CHoCH/MSS, liquidity sweeps, order blocks, FVG, CVD, VWAP, RSI divergence, funding/OI, and more — each weighted and combined into a normalized score.
- **Honest scoring engine**: score normalized over *active* modules only, with directional alignment and participation factors — silent data never dilutes the result.
- **Forward-test with backtest parity**: every published signal is tracked to close using the **same exit math as the backtest** (partial TPs, break-even moves, fees, funding), so live results are directly comparable to history. A dedicated endpoint reports a CONSISTENT/DIVERGENT verdict.
- **Validation suite**: walk-forward analysis, Monte-Carlo, bootstrap p-values, synthetic-data negative control — to separate real edge from overfitting on small samples.
- **Diagnostic analytics layers** (read-only, isolated from the trading logic): per-module/per-combination attribution with Wilson 95% confidence intervals and significance flags, entry-quality scoring, and a 6-factor market-filter diagnostic.
- **27 REST endpoints** (FastAPI + auto Swagger docs), async scanner, PostgreSQL persistence, Telegram delivery with auto-generated annotated charts (1600×900), and a clean config-driven design.

### Architecture
```
FastAPI (async) ── Scanner (60s loop) ── 20 SMC modules → 5 families → score 0–100 → grade A–D
       │                                         │
   27 REST endpoints                      Forward-test tracker (same math as backtest)
       │                                         │
   PostgreSQL ◄──────────── Backtest engine (walk-forward, Monte-Carlo, bootstrap)
       │                                         │
   Telegram bot (signals + annotated PNG charts, per-trade #ID tracking)
```

### What this demonstrates (for clients)
- Designing and shipping a **non-trivial async backend** (FastAPI + async SQLAlchemy + PostgreSQL).
- **Binance Futures API** integration (klines, funding, open interest) with graceful failure handling.
- **Quantitative methodology done right**: statistical rigor, anti-overfitting discipline, confidence intervals, forward-vs-backtest parity.
- Data visualization (matplotlib), Telegram bot delivery, Docker deployment, config-driven design.

> **Note on positioning:** this is a *research and analytics* system under active validation. It is not financial advice and makes no profitability guarantees — its value is the engineering and the honest, verifiable methodology.

### I'm available for similar work
Trading bots · backtesting engines · Binance/exchange API integrations · data pipelines · FastAPI backends · Telegram bots · trading dashboards.

---

## 🇷🇺 Русский

### Что это
Backend production-уровня, который анализирует фьючерсы Binance USDT-M по методологии **Smart Money Concepts (SMC)** и классическим индикаторам, оценивает каждый сетап по шкале 0–100 и проверяет собственную результативность форвард-тестом — а не маркетинговыми обещаниями. Построен как дисциплинированный квант-исследовательский пайплайн, а не бот «быстрого обогащения».

### Ключевые инженерные решения
- **20 аналитических модулей в 5 семействах сигнала** (структура, ликвидность, поток, настроение, импульс): market structure, BOS/CHoCH/MSS, liquidity sweeps, order blocks, FVG, CVD, VWAP, RSI-дивергенция, funding/OI и др. — каждый со своим весом, всё сводится в нормированный score.
- **Честный скоринг**: оценка нормируется только по *активным* модулям, с учётом согласованности направления и доли участия — «молчащие» данные не разбавляют результат.
- **Форвард-тест с паритетом бэктеста**: каждый опубликованный сигнал отслеживается до закрытия **той же математикой выходов, что и бэктест** (частичные TP, перевод в безубыток, комиссии, funding) — поэтому live-результаты напрямую сравнимы с историей. Отдельный эндпоинт выдаёт вердикт CONSISTENT/DIVERGENT.
- **Набор валидации**: walk-forward анализ, Monte-Carlo, bootstrap p-value, отрицательный контроль на синтетике — чтобы отделить реальное преимущество от переобучения на малых выборках.
- **Слои диагностической аналитики** (read-only, изолированы от торговой логики): атрибуция по модулям и комбинациям с доверительными интервалами Уилсона (95%) и флагами значимости, оценка качества входа, 6-факторная диагностика рыночных фильтров.
- **27 REST-эндпоинтов** (FastAPI + авто Swagger-документация), асинхронный сканер, хранение в PostgreSQL, доставка в Telegram с авто-генерацией размеченных графиков (1600×900), чистая конфигурируемая архитектура.

### Архитектура
```
FastAPI (async) ── Сканер (цикл 60с) ── 20 SMC-модулей → 5 семейств → score 0–100 → grade A–D
       │                                       │
   27 REST-эндпоинтов               Форвард-трекер (та же математика, что бэктест)
       │                                       │
   PostgreSQL ◄──────────── Движок бэктеста (walk-forward, Monte-Carlo, bootstrap)
       │                                       │
   Telegram-бот (сигналы + размеченные PNG-графики, отслеживание по #ID сделки)
```

### Что это демонстрирует (для заказчиков)
- Проектирование и запуск **нетривиального асинхронного backend** (FastAPI + async SQLAlchemy + PostgreSQL).
- Интеграция с **Binance Futures API** (klines, funding, open interest) с корректной обработкой сбоев.
- **Грамотная квантовая методология**: статистическая строгость, дисциплина против оверфита, доверительные интервалы, паритет forward↔backtest.
- Визуализация данных (matplotlib), доставка через Telegram-бот, развёртывание в Docker, конфигурируемый дизайн.

> **О позиционировании:** это *исследовательско-аналитическая* система на стадии валидации. Не является финансовой рекомендацией и не гарантирует прибыль — её ценность в инженерии и честной, проверяемой методологии.

### Доступен для похожих задач
Торговые боты · движки бэктестинга · интеграции с API бирж (Binance и др.) · дата-пайплайны · backend на FastAPI · Telegram-боты · торговые дашборды.

---

## Tech notes
- **Backend:** FastAPI 0.115, async SQLAlchemy 2.0, PostgreSQL (asyncpg)
- **Data/analysis:** pandas 2.2, numpy 2.2, custom SMC indicators
- **Delivery:** Telegram Bot API, matplotlib chart rendering
- **Infra:** Docker-ready, config-driven (.env), 27 documented REST endpoints (Swagger/OpenAPI)

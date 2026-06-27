# Real Binance Data Audit — готовность к Cross-Symbol Statistical Truth на реальных данных

**Режим:** только аудит текущей кодовой базы (v9). Логика стратегии не менялась, оптимизация не выполнялась. Примечание: прежняя версия этого отчёта (из ранней сессии) описывала несуществующий флаг `--real` — данный документ сверен с фактическим кодом построчно.

---

## 1. Существующие методы загрузки данных

| Метод (`BinanceFuturesClient`) | Эндпоинт | Назначение | Статус |
|---|---|---|---|
| `klines(symbol, interval, limit≤1500, start, end)` | `/fapi/v1/klines` | Свечи, одна страница | ✅ live-сканер |
| `klines_history(symbol, interval, start, end)` | `/fapi/v1/klines` | **Пагинация любой глубины** (шаг 1500, sleep 0.25с, дедуп) | ✅ backtester, statistical_truth |
| `funding_rate(symbol, limit=30)` | `/fapi/v1/fundingRate` | Последние N ставок | ✅ live-сканер |
| `funding_rate_history(symbol, start, end)` | `/fapi/v1/fundingRate` | **Пагинация любой глубины** (1000 зап. ≈ 333 дня/запрос) | ✅ backtester, fetch_real |
| `funding_history_paginated(symbol, start)` | `/fapi/v1/fundingRate` | Дубликат предыдущего (ранняя версия, без `end`) | ⚠️ мёртвый код, нигде не вызывается |
| `open_interest_hist(symbol, period, limit≤500)` | `/futures/data/openInterestHist` | История OI | ✅ live; ❌ для 24 мес непригоден (п.4) |
| `mark_price(symbol)` | `/fapi/v1/premiumIndex` | Mark-цена | ✅ трекинг TP/SL |

Ошибки: 429 → ретрай с backoff (3 попытки). ⚠️ 418 (бан IP) и 451 (гео) не обрабатываются отдельно — упадут как HTTP-ошибка; при штатных паузах 418 недостижим.

## 2. Что реально загружается (проверено по вызовам)

| Потребитель | klines | funding | OI | Куда попадает |
|---|---|---|---|---|
| Live-сканер | LTF 400 + HTF 300 | 30 последних | 96 точек | ctx → все 20 модулей живые |
| `run_backtest` (POST /backtest/run) | history весь период | history весь период | — | оба в ctx (срез по бару, без look-ahead); funding также в CostModel |
| `statistical_truth` real-режим | history 1h | history весь период | — | klines → ctx; **funding → только CostModel** (GAP-1) |
| `cross_symbol_truth` / `cross_symbol_validation` | синтетика | синтетика | — | реального режима нет (GAP-2) |

## 3. Чего не хватает

**GAP-1 (главный):** `scripts/statistical_truth.py:199` — real-режим скачивает funding, но не передаёт его в `precompute_candidates` (фикс v7 применён к `backtester.run_backtest`, сюда — нет). Без патча прогон корректен (no_data исключается из знаменателя семейства), но funding-модуль будет нем, sentiment-семейство — только на liquidation. Патч одной строкой — п.6 шаг 3.

**GAP-2:** скрипты cross_symbol_* — только синтетический источник. Для реала: запускать `statistical_truth` (real-режим полный) либо патч из п.6 шаг 5.

**GAP-3 (структурный):** исторический OI за 24 мес недостижим через REST (п.4). Модули open_interest/oi_delta в реальном бэктесте честно no_data. Пути на будущее: (а) собственный коллектор с текущего момента; (б) bulk-архивы `data.binance.vision` → `data/futures/um/daily/metrics/<SYMBOL>/` — ежедневные CSV с `sum_open_interest` (5-мин гранулярность, глубина с ~конца 2021; ~730 файлов/символ/24 мес). Проверка доступности: `curl -sI https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2025-01-01.zip` → ждём HTTP 200.

**GAP-4 (минорные):** «24 месяца» = `months*30` = 720 дней; реальных данных по ликвидациям нет (стрим forceOrder урезан Binance с 2021, история — только платные провайдеры); дубликат funding-метода (п.1).

## 4. Ограничения Binance API для 24 месяцев

| Данные | Лимит/запрос | Глубина истории | Вес | Вывод |
|---|---|---|---|---|
| klines | 1500 | с листинга (все 5 символов > 24 мес: BTC/ETH 2019, XRP/BNB нач. 2020, SOL 09.2020) | 10 | ✅ пагинацией |
| fundingRate | 1000 (≈333 дня) | полная | 1 | ✅ 3 страницы |
| openInterestHist | 500 | **только ~30 последних дней** | 1 | ❌ REST не даёт 24 мес; обход — Vision (GAP-3) |
| Ликвидации | — | публичной истории нет | — | ❌ |
| Rate limit IP | 2400 веса/мин | — | — | наш профиль ~615 веса — запас ×4 |

API-ключи не нужны (всё публичное). При HTTP 451 (гео-блок) klines доступны архивами `data.binance.vision`.

## 5. Запросы на полный прогон (24 мес, 1h)

На символ: klines ~17 280–17 520 свечей → **12 запросов** (вес 120); funding ~2 190 записей → **3 запроса**. Итого **15 запросов / 123 веса**.

| | BTC | ETH | SOL | BNB | XRP | Всего |
|---|---|---|---|---|---|---|
| klines | 12 | 12 | 12 | 12 | 12 | 60 |
| funding | 3 | 3 | 3 | 3 | 3 | 15 |
| **Запросов** | 15 | 15 | 15 | 15 | 15 | **75** |
| Вес | 123 | 123 | 123 | 123 | 123 | **615** (26% лимита/мин) |

Загрузка со встроенными паузами: ~20–40 с/символ → ~2–3 мин всё. Полный прогон (загрузка + прекомпьют + WFA + Monte-Carlo, 5 символов): **~35–50 мин**, RAM < 1 ГБ.

## 6. Пошаговая инструкция запуска

**Шаг 1. Окружение** (машина с доступом к fapi.binance.com):
```bash
unzip crypto_signals_v9.zip && cd crypto_signals/backend
python3 -m venv venv && source venv/bin/activate     # Win: venv\Scripts\activate
pip install -r requirements.txt
```

**Шаг 2. Проверка связности:**
```bash
python - << 'PY'
import asyncio
from app.binance.client import get_client
async def main():
    c = get_client()
    df = await c.klines("BTCUSDT", "1h", limit=5)
    f = await c.funding_rate("BTCUSDT", limit=3)
    print("klines:", len(df), "| последняя:", df["close_time"].iloc[-1])
    print("funding:", float(f["fundingRate"].iloc[-1]))
    await c.close()
asyncio.run(main())
PY
```
Ожидаемо: 5 свежих свечей. 451 → регион ограничен (п.4).

**Шаг 3. Патч GAP-1** (одна строка данных, не логика). В `scripts/statistical_truth.py` заменить строку 199:
```python
# было:
cands = precompute_candidates(symbol, "1h", df, enforce_htf=True)
# стало:
f_df = (funding.rename("fundingRate").reset_index()
        .rename(columns={"index": "fundingTime"})) if not funding.empty else None
cands = precompute_candidates(symbol, "1h", df, enforce_htf=True, funding_df=f_df)
```

**Шаг 4. Основной прогон** (WFA + Monte-Carlo + формальный вердикт):
```bash
python -m scripts.statistical_truth \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT \
  --months 24 --out REAL_STATISTICAL_TRUTH.md
```

**Шаг 5 (опц.). Cross-Symbol Truth с атрибуцией модулей** — патч GAP-2 в `analyze()` файла `scripts/cross_symbol_truth.py`:
```python
# было:
df = make_synthetic(symbol, MONTHS, seed=seed)
funding = synth_funding(df, seed + 1)
# стало:
import asyncio as _a
from scripts.statistical_truth import fetch_real
df, f_series = _a.run(fetch_real(symbol, MONTHS))
funding = (f_series.rename("fundingRate").reset_index()
           .rename(columns={"index": "fundingTime"}))
```
Затем `python -m scripts.cross_symbol_truth`.

**Шаг 6. Интерпретация по двум эталонам:** вердикт edge — критерии из `STATISTICAL_TRUTH_REPORT.md` (≥100 OOS-сделок, exp>0 после издержек, PF>1.10, p<0.05, MC prob≥0.90 — на ≥4/5 символов); устойчивость модулей сравнивать с контрольным `CROSS_SYMBOL_TRUTH.md`: модуль ценен, если стабилен на реале **и не был** стабильно «полезен» на шуме.

**Чек-лист:** ✅ klines 24м • ✅ funding 24м • ✅ funding→ctx в run_backtest • ⚠️ GAP-1 в statistical_truth (патч шага 3) • ⚠️ GAP-2 в cross_symbol-скриптах (шаг 5) • ❌ OI 24м через REST (no_data учтён) • ✅ симулятор/WFA/MC протестированы • ✅ лимиты API: запас ×4 • ✅ ключи не нужны.

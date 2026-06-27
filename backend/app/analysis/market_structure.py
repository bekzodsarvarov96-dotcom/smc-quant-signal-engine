"""Market Structure: HH/HL/LH/LL, BOS, CHOCH.

Логика:
  • Размечаем последовательность swing high/low.
  • HH+HL подряд → восходящая структура; LH+LL → нисходящая.
  • BOS  — пробой экстремума ПО тренду (continuation).
  • CHOCH — первый пробой ПРОТИВ текущей структуры (возможный разворот).
"""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult, get_swing_points


def _label_swings(highs: list, lows: list) -> list[dict]:
    """Объединяет swing-точки в хронологию и присваивает метки HH/HL/LH/LL."""
    points = [{"idx": i, "price": p, "type": "H"} for i, p in highs]
    points += [{"idx": i, "price": p, "type": "L"} for i, p in lows]
    points.sort(key=lambda x: x["idx"])

    last_high = last_low = None
    for pt in points:
        if pt["type"] == "H":
            pt["label"] = "HH" if (last_high is not None and pt["price"] > last_high) else "LH"
            last_high = pt["price"]
        else:
            pt["label"] = "HL" if (last_low is not None and pt["price"] > last_low) else "LL"
            last_low = pt["price"]
    return points


def detect_structure(ctx: MarketContext) -> dict:
    """Возвращает состояние структуры; кэшируется в ctx.extras."""
    if "structure" in ctx.extras:
        return ctx.extras["structure"]

    swings = get_swing_points(ctx)
    points = _label_swings(swings["highs"], swings["lows"])
    df = ctx.df
    close = df["close"].iloc[-1]

    recent = points[-8:]
    labels = [p["label"] for p in recent]

    # Тренд по последним меткам
    up_votes = labels.count("HH") + labels.count("HL")
    down_votes = labels.count("LH") + labels.count("LL")
    trend = "up" if up_votes > down_votes else "down" if down_votes > up_votes else "range"

    last_swing_high = next((p for p in reversed(points) if p["type"] == "H"), None)
    last_swing_low = next((p for p in reversed(points) if p["type"] == "L"), None)

    bos = choch = None
    if last_swing_high and close > last_swing_high["price"]:
        bos = "up" if trend == "up" else None
        choch = "up" if trend == "down" else None
    if last_swing_low and close < last_swing_low["price"]:
        bos = "down" if trend == "down" else bos
        choch = "down" if trend == "up" else choch

    state = {
        "trend": trend,
        "labels": labels,
        "points": points,
        "bos": bos,
        "choch": choch,
        "last_swing_high": last_swing_high["price"] if last_swing_high else None,
        "last_swing_low": last_swing_low["price"] if last_swing_low else None,
    }
    ctx.extras["structure"] = state
    return state


class MarketStructureModule(AnalysisModule):
    name = "market_structure"
    weight = 1.6

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        st = detect_structure(ctx)
        reasons: list[str] = []
        bias = 0.0
        score = 0.0

        if st["trend"] == "up":
            bias, score = 1.0, 0.6
            reasons.append(f"Восходящая структура ({' → '.join(st['labels'][-4:])})")
        elif st["trend"] == "down":
            bias, score = -1.0, 0.6
            reasons.append(f"Нисходящая структура ({' → '.join(st['labels'][-4:])})")
        else:
            return self.neutral("Структура в боковике — направление не определено")

        return ModuleResult(self.name, bias, score, reasons)


class BOSModule(AnalysisModule):
    name = "bos"
    weight = 1.4

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        st = detect_structure(ctx)
        if st["bos"] == "up":
            return ModuleResult(self.name, 1.0, 0.85,
                                [f"BOS вверх: пробой swing high {st['last_swing_high']:.6g}"])
        if st["bos"] == "down":
            return ModuleResult(self.name, -1.0, 0.85,
                                [f"BOS вниз: пробой swing low {st['last_swing_low']:.6g}"])
        return self.neutral()


class CHOCHModule(AnalysisModule):
    name = "choch"
    weight = 1.5

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        st = detect_structure(ctx)
        if st["choch"] == "up":
            return ModuleResult(self.name, 1.0, 0.9,
                                ["CHOCH: слом нисходящей структуры — смена характера вверх"])
        if st["choch"] == "down":
            return ModuleResult(self.name, -1.0, 0.9,
                                ["CHOCH: слом восходящей структуры — смена характера вниз"])
        return self.neutral()

"""Семейства факторов.

«Независимое подтверждение» = подтверждение от РАЗНОГО семейства,
а не от разных модулей (модули внутри семейства коррелированы).
"""

FAMILIES: dict[str, list[str]] = {
    # Тренд и структура (всё, что измеряет направление рынка)
    "trend": ["ema_trend", "mtf_trend", "market_structure", "bos", "choch", "mss"],
    # Ликвидность и зоны интереса (SMC-зоны)
    "liquidity": ["liquidity_sweep", "equal_highs_lows", "order_block",
                  "fvg", "fibonacci", "volume_profile"],
    # Поток ордеров и позиционирование
    "flow": ["volume", "cvd", "vwap", "open_interest", "oi_delta"],
    # Настроение деривативов
    "sentiment": ["funding_rate", "liquidation_clusters"],
    # Импульс
    "momentum": ["rsi_divergence"],
}

MODULE_TO_FAMILY: dict[str, str] = {
    m: fam for fam, mods in FAMILIES.items() for m in mods
}

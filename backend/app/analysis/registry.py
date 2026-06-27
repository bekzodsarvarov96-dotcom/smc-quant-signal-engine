"""Реестр модулей анализа.

Чтобы добавить новый алгоритм:
  1) файл в app/analysis/ с классом-наследником AnalysisModule;
  2) экземпляр в ALL_MODULES — движок, сканер, бэктестер и API
     подхватят его автоматически.

ВАЖНО: MultiTimeframeModule должен идти ПЕРВЫМ — он кладёт htf_trend
в ctx.extras, который используют другие модули и жёсткий фильтр движка.
"""
from .base import AnalysisModule
from .mtf import MultiTimeframeModule
from .market_structure import MarketStructureModule, BOSModule, CHOCHModule
from .mss import MSSModule
from .liquidity import EqualHighsLowsModule, LiquiditySweepModule
from .order_blocks import OrderBlockModule
from .fvg import FVGModule
from .fibonacci import FibonacciModule
from .ema import EMAModule
from .volume import VolumeModule
from .volume_profile import VolumeProfileModule
from .vwap import VWAPModule
from .cvd import CVDModule
from .open_interest import OpenInterestModule
from .oi_delta import OIDeltaModule
from .funding import FundingRateModule
from .liquidation import LiquidationClusterModule
from .rsi_divergence import RSIDivergenceModule

ALL_MODULES: list[AnalysisModule] = [
    MultiTimeframeModule(),        # первым: определяет htf_trend
    MarketStructureModule(),
    BOSModule(),
    CHOCHModule(),
    MSSModule(),
    EqualHighsLowsModule(),
    LiquiditySweepModule(),
    OrderBlockModule(),
    FVGModule(),
    FibonacciModule(),
    EMAModule(),
    VolumeModule(),
    VolumeProfileModule(),
    VWAPModule(),
    CVDModule(),
    OpenInterestModule(),
    OIDeltaModule(),
    FundingRateModule(),
    LiquidationClusterModule(),
    RSIDivergenceModule(),
]


def get_modules() -> list[AnalysisModule]:
    """Активные модули с учётом DISABLED_MODULES из конфига.

    ВНИМАНИЕ: отключение mtf_trend убирает определение htf_trend и тем
    самым деактивирует жёсткий фильтр против старшего тренда.
    """
    from ..config import get_settings
    disabled = get_settings().disabled_module_set
    if not disabled:
        return ALL_MODULES
    return [m for m in ALL_MODULES if m.name not in disabled]


def all_module_names() -> list[str]:
    return [m.name for m in ALL_MODULES]

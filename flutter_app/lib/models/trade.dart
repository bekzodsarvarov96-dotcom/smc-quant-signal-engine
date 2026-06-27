class Trade {
  final int id;
  final int? signalId;
  final String symbol;
  final String direction;
  final double entryPrice;
  final double? exitPrice;
  final double stopLoss;
  final double? pnl;
  final double? pnlR;
  final DateTime openedAt;
  final DateTime? closedAt;
  final bool isOpen;
  final String? notes;

  Trade({
    required this.id,
    this.signalId,
    required this.symbol,
    required this.direction,
    required this.entryPrice,
    this.exitPrice,
    required this.stopLoss,
    this.pnl,
    this.pnlR,
    required this.openedAt,
    this.closedAt,
    required this.isOpen,
    this.notes,
  });

  factory Trade.fromJson(Map<String, dynamic> j) => Trade(
        id: j['id'],
        signalId: j['signal_id'],
        symbol: j['symbol'],
        direction: j['direction'],
        entryPrice: (j['entry_price'] as num).toDouble(),
        exitPrice: (j['exit_price'] as num?)?.toDouble(),
        stopLoss: (j['stop_loss'] as num).toDouble(),
        pnl: (j['pnl'] as num?)?.toDouble(),
        pnlR: (j['pnl_r'] as num?)?.toDouble(),
        openedAt: DateTime.parse(j['opened_at']),
        closedAt: j['closed_at'] != null ? DateTime.parse(j['closed_at']) : null,
        isOpen: j['is_open'],
        notes: j['notes'],
      );
}

class Stats {
  final int totalTrades;
  final int openTrades;
  final int closedTrades;
  final int wins;
  final int losses;
  final double winrate;
  final double totalPnl;
  final double totalR;
  final double avgR;
  final double bestR;
  final double worstR;
  final double profitFactor;

  Stats({
    required this.totalTrades,
    required this.openTrades,
    required this.closedTrades,
    required this.wins,
    required this.losses,
    required this.winrate,
    required this.totalPnl,
    required this.totalR,
    required this.avgR,
    required this.bestR,
    required this.worstR,
    required this.profitFactor,
  });

  factory Stats.fromJson(Map<String, dynamic> j) => Stats(
        totalTrades: j['total_trades'],
        openTrades: j['open_trades'],
        closedTrades: j['closed_trades'],
        wins: j['wins'],
        losses: j['losses'],
        winrate: (j['winrate'] as num).toDouble(),
        totalPnl: (j['total_pnl'] as num).toDouble(),
        totalR: (j['total_r'] as num).toDouble(),
        avgR: (j['avg_r'] as num).toDouble(),
        bestR: (j['best_r'] as num).toDouble(),
        worstR: (j['worst_r'] as num).toDouble(),
        profitFactor: (j['profit_factor'] as num).toDouble(),
        avgRR: (j['avg_rr'] as num? ?? 0).toDouble(),
        expectedValue: (j['expected_value'] as num? ?? 0).toDouble(),
      );
}

class BacktestResult {
  final int id;
  final String symbol;
  final String timeframe;
  final int totalSignals;
  final int wins;
  final int losses;
  final double winrate;
  final double totalR;
  final double avgR;
  final double maxDrawdownR;
  final double profitFactor;
  final double avgRR;
  final double expectedValue;
  final List<dynamic> trades;

  BacktestResult({
    required this.id,
    required this.symbol,
    required this.timeframe,
    required this.totalSignals,
    required this.wins,
    required this.losses,
    required this.winrate,
    required this.totalR,
    required this.avgR,
    required this.maxDrawdownR,
    required this.profitFactor,
    required this.avgRR,
    required this.expectedValue,
    required this.trades,
  });

  factory BacktestResult.fromJson(Map<String, dynamic> j) => BacktestResult(
        id: j['id'],
        symbol: j['symbol'],
        timeframe: j['timeframe'],
        totalSignals: j['total_signals'],
        wins: j['wins'],
        losses: j['losses'],
        winrate: (j['winrate'] as num).toDouble(),
        totalR: (j['total_r'] as num).toDouble(),
        avgR: (j['avg_r'] as num).toDouble(),
        maxDrawdownR: (j['max_drawdown_r'] as num).toDouble(),
        profitFactor: (j['profit_factor'] as num).toDouble(),
        avgRR: (j['avg_rr'] as num? ?? 0).toDouble(),
        expectedValue: (j['expected_value'] as num? ?? 0).toDouble(),
        trades: j['trades'] ?? [],
      );
}

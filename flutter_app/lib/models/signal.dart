class Signal {
  final int id;
  final DateTime createdAt;
  final String symbol;
  final String timeframe;
  final String direction;
  final double score;
  final String grade;
  final double probability;
  final int confirmations;
  final double entry;
  final double stopLoss;
  final double tp1;
  final double tp2;
  final double tp3;
  final Map<String, dynamic> reasons;
  final Map<String, dynamic> moduleScores;
  final String status;

  Signal({
    required this.id,
    required this.createdAt,
    required this.symbol,
    required this.timeframe,
    required this.direction,
    required this.score,
    required this.grade,
    required this.probability,
    required this.confirmations,
    required this.entry,
    required this.stopLoss,
    required this.tp1,
    required this.tp2,
    required this.tp3,
    required this.reasons,
    required this.moduleScores,
    required this.status,
  });

  bool get isLong => direction == 'LONG';

  factory Signal.fromJson(Map<String, dynamic> j) => Signal(
        id: j['id'],
        createdAt: DateTime.parse(j['created_at']),
        symbol: j['symbol'],
        timeframe: j['timeframe'],
        direction: j['direction'],
        score: (j['score'] as num).toDouble(),
        grade: j['grade'] ?? 'B',
        probability: (j['probability'] as num).toDouble(),
        confirmations: j['confirmations'],
        entry: (j['entry'] as num).toDouble(),
        stopLoss: (j['stop_loss'] as num).toDouble(),
        tp1: (j['tp1'] as num).toDouble(),
        tp2: (j['tp2'] as num).toDouble(),
        tp3: (j['tp3'] as num).toDouble(),
        reasons: Map<String, dynamic>.from(j['reasons'] ?? {}),
        moduleScores: Map<String, dynamic>.from(j['module_scores'] ?? {}),
        status: j['status'],
      );
}

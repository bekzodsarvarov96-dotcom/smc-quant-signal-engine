import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../models/signal.dart';

const longColor = Color(0xFF26A69A);
const shortColor = Color(0xFFEF5350);

class SignalCard extends StatelessWidget {
  final Signal signal;
  final VoidCallback onTap;

  const SignalCard({super.key, required this.signal, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final color = signal.isLong ? longColor : shortColor;
    final dt = DateFormat('dd.MM HH:mm').format(signal.createdAt.toLocal());

    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    decoration: BoxDecoration(
                      color: color.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: color),
                    ),
                    child: Text(signal.direction,
                        style: TextStyle(
                            color: color, fontWeight: FontWeight.bold)),
                  ),
                  const SizedBox(width: 10),
                  Text(signal.symbol,
                      style: const TextStyle(
                          fontSize: 17, fontWeight: FontWeight.w600)),
                  const Spacer(),
                  _GradeBadge(grade: signal.grade),
                  const SizedBox(width: 6),
                  _ScoreBadge(score: signal.score),
                ],
              ),
              const SizedBox(height: 10),
              Row(
                children: [
                  _kv('Вход', signal.entry),
                  _kv('Стоп', signal.stopLoss),
                  _kv('TP2', signal.tp2),
                ],
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  Text('$dt • ${signal.timeframe} • '
                      'семейств: ${signal.confirmations} • '
                      'P=${(signal.probability * 100).toStringAsFixed(0)}%',
                      style: TextStyle(
                          fontSize: 12, color: Colors.grey.shade500)),
                  const Spacer(),
                  _StatusChip(status: signal.status),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _kv(String k, double v) => Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(k,
                style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
            Text(_fmt(v),
                style: const TextStyle(
                    fontSize: 14, fontFeatures: [FontFeature.tabularFigures()])),
          ],
        ),
      );

  static String _fmt(double v) =>
      v >= 100 ? v.toStringAsFixed(2) : v.toStringAsFixed(v >= 1 ? 4 : 6);
}

class _ScoreBadge extends StatelessWidget {
  final double score;
  const _ScoreBadge({required this.score});

  @override
  Widget build(BuildContext context) {
    final color = score >= 80
        ? const Color(0xFF26A69A)
        : score >= 65
            ? const Color(0xFFFFB74D)
            : Colors.grey;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withOpacity(0.15),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text('${score.toStringAsFixed(0)}/100',
          style: TextStyle(color: color, fontWeight: FontWeight.bold)),
    );
  }
}

class _StatusChip extends StatelessWidget {
  final String status;
  const _StatusChip({required this.status});

  @override
  Widget build(BuildContext context) {
    final map = {
      'ACTIVE': ('Активен', Colors.blueGrey),
      'TP1_HIT': ('TP1 ✓', longColor),
      'TP2_HIT': ('TP2 ✓', longColor),
      'TP3_HIT': ('TP3 ✓', longColor),
      'STOPPED': ('Стоп', shortColor),
      'EXPIRED': ('Истёк', Colors.grey),
    };
    final (label, color) = map[status] ?? (status, Colors.grey);
    return Text(label,
        style: TextStyle(
            fontSize: 12, color: color, fontWeight: FontWeight.w600));
  }
}


class _GradeBadge extends StatelessWidget {
  final String grade;
  const _GradeBadge({required this.grade});

  static const _colors = {
    'A+': Color(0xFF00C853),
    'A': Color(0xFF26A69A),
    'B': Color(0xFFFFB74D),
    'C': Color(0xFF90A4AE),
    'D': Color(0xFF757575),
  };

  @override
  Widget build(BuildContext context) {
    final color = _colors[grade] ?? Colors.grey;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withOpacity(0.2),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: color),
      ),
      child: Text(grade,
          style: TextStyle(color: color, fontWeight: FontWeight.bold)),
    );
  }
}

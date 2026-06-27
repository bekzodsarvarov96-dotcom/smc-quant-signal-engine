import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/signal.dart';
import '../services/api_service.dart';
import '../widgets/signal_card.dart' show longColor, shortColor;

class SignalDetailScreen extends StatelessWidget {
  final Signal signal;
  const SignalDetailScreen({super.key, required this.signal});

  @override
  Widget build(BuildContext context) {
    final color = signal.isLong ? longColor : shortColor;
    return Scaffold(
      appBar: AppBar(title: Text('${signal.direction} ${signal.symbol}')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _ScoreHeader(signal: signal, color: color),
          const SizedBox(height: 16),
          _LevelsCard(signal: signal),
          const SizedBox(height: 16),
          Text('Причины формирования сигнала',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          ...signal.reasons.entries.expand((e) sync* {
            for (final r in (e.value as List)) {
              yield Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(Icons.check_circle_outline, size: 18, color: color),
                    const SizedBox(width: 8),
                    Expanded(child: Text(r.toString())),
                  ],
                ),
              );
            }
          }),
          const SizedBox(height: 24),
          FilledButton.icon(
            icon: const Icon(Icons.add_chart),
            label: const Text('Добавить в журнал сделок'),
            onPressed: () async {
              try {
                await ApiService().openTradeFromSignal(signal);
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Сделка добавлена в журнал')));
                }
              } catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(context)
                      .showSnackBar(SnackBar(content: Text('Ошибка: $e')));
                }
              }
            },
          ),
          const SizedBox(height: 12),
          Text(
            'Сигналы носят аналитический характер и не являются '
            'финансовой рекомендацией. Управляйте риском.',
            style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}

class _ScoreHeader extends StatelessWidget {
  final Signal signal;
  final Color color;
  const _ScoreHeader({required this.signal, required this.color});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            SizedBox(
              width: 72,
              height: 72,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  CircularProgressIndicator(
                    value: signal.score / 100,
                    strokeWidth: 6,
                    color: color,
                    backgroundColor: Colors.white12,
                  ),
                  Center(
                      child: Text(signal.score.toStringAsFixed(0),
                          style: const TextStyle(
                              fontSize: 20, fontWeight: FontWeight.bold))),
                ],
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Грейд ${signal.grade} • Score ${signal.score.toStringAsFixed(0)}/100',
                      style: const TextStyle(
                          fontSize: 16, fontWeight: FontWeight.w600)),
                  Text(
                      'Вероятность ${(signal.probability * 100).toStringAsFixed(0)}% '
                      '• семейств-подтверждений: ${signal.confirmations}'),
                  Text('${signal.timeframe} • статус: ${signal.status}',
                      style: TextStyle(
                          fontSize: 12, color: Colors.grey.shade500)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _LevelsCard extends StatelessWidget {
  final Signal signal;
  const _LevelsCard({required this.signal});

  @override
  Widget build(BuildContext context) {
    final rows = [
      ('Точка входа', signal.entry, Colors.white),
      ('Стоп-лосс', signal.stopLoss, shortColor),
      ('TP1 (1R)', signal.tp1, longColor),
      ('TP2 (2R)', signal.tp2, longColor),
      ('TP3 (3R)', signal.tp3, longColor),
    ];
    return Card(
      child: Column(
        children: rows
            .map((r) => ListTile(
                  dense: true,
                  title: Text(r.$1),
                  trailing: GestureDetector(
                    onLongPress: () {
                      Clipboard.setData(
                          ClipboardData(text: r.$2.toString()));
                      ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('Скопировано')));
                    },
                    child: Text(
                      r.$2.toStringAsFixed(r.$2 >= 100 ? 2 : 5),
                      style: TextStyle(
                          color: r.$3,
                          fontSize: 15,
                          fontWeight: FontWeight.w600),
                    ),
                  ),
                ))
            .toList(),
      ),
    );
  }
}

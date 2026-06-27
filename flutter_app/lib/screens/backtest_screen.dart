import 'package:flutter/material.dart';

import '../models/trade.dart';
import '../services/api_service.dart';
import '../widgets/signal_card.dart' show longColor, shortColor;

class BacktestScreen extends StatefulWidget {
  const BacktestScreen({super.key});

  @override
  State<BacktestScreen> createState() => _BacktestScreenState();
}

class _BacktestScreenState extends State<BacktestScreen> {
  final _api = ApiService();
  final _symbolCtrl = TextEditingController(text: 'BTCUSDT');
  String _timeframe = '15m';
  int _days = 90;
  bool _running = false;
  BacktestResult? _result;
  String? _error;

  Future<void> _run() async {
    setState(() {
      _running = true;
      _error = null;
      _result = null;
    });
    try {
      final r = await _api.runBacktest(
        symbol: _symbolCtrl.text.trim().toUpperCase(),
        timeframe: _timeframe,
        days: _days,
      );
      setState(() => _result = r);
    } catch (e) {
      setState(() => _error = e.toString());
    } finally {
      setState(() => _running = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Backtesting')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
            controller: _symbolCtrl,
            decoration: const InputDecoration(
                labelText: 'Символ', border: OutlineInputBorder()),
            textCapitalization: TextCapitalization.characters,
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: DropdownButtonFormField<String>(
                  value: _timeframe,
                  decoration: const InputDecoration(
                      labelText: 'Таймфрейм', border: OutlineInputBorder()),
                  items: const ['5m', '15m', '30m', '1h', '4h']
                      .map((t) => DropdownMenuItem(value: t, child: Text(t)))
                      .toList(),
                  onChanged: (v) => setState(() => _timeframe = v!),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: DropdownButtonFormField<int>(
                  value: _days,
                  decoration: const InputDecoration(
                      labelText: 'Дней истории', border: OutlineInputBorder()),
                  items: const [30, 90, 180, 365, 730]
                      .map((d) =>
                          DropdownMenuItem(value: d, child: Text('$d')))
                      .toList(),
                  onChanged: (v) => setState(() => _days = v!),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: _running ? null : _run,
            icon: _running
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.play_arrow),
            label: Text(_running ? 'Выполняется…' : 'Запустить бэктест'),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: 16),
              child: Text('Ошибка: $_error',
                  style: const TextStyle(color: shortColor)),
            ),
          if (_result != null) _ResultView(result: _result!),
        ],
      ),
    );
  }
}

class _ResultView extends StatelessWidget {
  final BacktestResult result;
  const _ResultView({required this.result});

  @override
  Widget build(BuildContext context) {
    Widget row(String k, String v, [Color? c]) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(k, style: TextStyle(color: Colors.grey.shade400)),
              Text(v,
                  style: TextStyle(fontWeight: FontWeight.w600, color: c)),
            ],
          ),
        );

    return Card(
      margin: const EdgeInsets.only(top: 20),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${result.symbol} • ${result.timeframe}',
                style: Theme.of(context).textTheme.titleMedium),
            const Divider(),
            row('Сигналов', '${result.totalSignals}'),
            row('Winrate', '${result.winrate.toStringAsFixed(1)}%',
                result.winrate >= 50 ? longColor : shortColor),
            row('Побед / поражений', '${result.wins} / ${result.losses}'),
            row('Суммарный результат',
                '${result.totalR >= 0 ? '+' : ''}${result.totalR.toStringAsFixed(2)}R',
                result.totalR >= 0 ? longColor : shortColor),
            row('Средний R на сделку', result.avgR.toStringAsFixed(3)),
            row('Average RR (победители)', result.avgRR.toStringAsFixed(2)),
            row(
                'Expected Value',
                '${result.expectedValue >= 0 ? '+' : ''}'
                    '${result.expectedValue.toStringAsFixed(3)}R/сделка',
                result.expectedValue >= 0 ? longColor : shortColor),
            row('Profit Factor', result.profitFactor.toStringAsFixed(2)),
            row('Max Drawdown', '${result.maxDrawdownR.toStringAsFixed(2)}R',
                shortColor),
          ],
        ),
      ),
    );
  }
}

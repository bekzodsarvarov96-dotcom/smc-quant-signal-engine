import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../models/trade.dart';
import '../services/api_service.dart';
import '../widgets/signal_card.dart' show longColor, shortColor;

class JournalScreen extends StatefulWidget {
  const JournalScreen({super.key});

  @override
  State<JournalScreen> createState() => _JournalScreenState();
}

class _JournalScreenState extends State<JournalScreen> {
  final _api = ApiService();
  List<Trade> _trades = [];
  Stats? _stats;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final results = await Future.wait([_api.fetchTrades(), _api.fetchStats()]);
      setState(() {
        _trades = results[0] as List<Trade>;
        _stats = results[1] as Stats;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      setState(() {
        _loading = false;
        _error = e.toString();
      });
    }
  }

  Future<void> _closeDialog(Trade t) async {
    final controller = TextEditingController();
    final price = await showDialog<double>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Закрыть ${t.direction} ${t.symbol}'),
        content: TextField(
          controller: controller,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(labelText: 'Цена выхода'),
          autofocus: true,
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx), child: const Text('Отмена')),
          FilledButton(
            onPressed: () =>
                Navigator.pop(ctx, double.tryParse(controller.text)),
            child: const Text('Закрыть сделку'),
          ),
        ],
      ),
    );
    if (price == null) return;
    try {
      await _api.closeTrade(t.id, price);
      _load();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Ошибка: $e')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Журнал сделок'),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _load)
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text('Ошибка: $_error'))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    children: [
                      if (_stats != null) _StatsPanel(stats: _stats!),
                      ..._trades.map((t) => _TradeTile(
                            trade: t,
                            onClose: () => _closeDialog(t),
                          )),
                      if (_trades.isEmpty)
                        const Padding(
                          padding: EdgeInsets.all(48),
                          child: Center(child: Text('Журнал пуст')),
                        ),
                    ],
                  ),
                ),
    );
  }
}

class _StatsPanel extends StatelessWidget {
  final Stats stats;
  const _StatsPanel({required this.stats});

  @override
  Widget build(BuildContext context) {
    Widget cell(String label, String value, [Color? color]) => Expanded(
          child: Column(
            children: [
              Text(value,
                  style: TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.bold,
                      color: color)),
              Text(label,
                  style:
                      TextStyle(fontSize: 11, color: Colors.grey.shade500)),
            ],
          ),
        );

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          children: [
            Row(children: [
              cell('Winrate', '${stats.winrate.toStringAsFixed(1)}%',
                  stats.winrate >= 50 ? longColor : shortColor),
              cell('Сделок', '${stats.closedTrades}'),
              cell('Открыто', '${stats.openTrades}'),
            ]),
            const SizedBox(height: 12),
            Row(children: [
              cell('Σ R', stats.totalR.toStringAsFixed(2),
                  stats.totalR >= 0 ? longColor : shortColor),
              cell('Avg R', stats.avgR.toStringAsFixed(2)),
              cell('PF', stats.profitFactor.toStringAsFixed(2)),
            ]),
          ],
        ),
      ),
    );
  }
}

class _TradeTile extends StatelessWidget {
  final Trade trade;
  final VoidCallback onClose;
  const _TradeTile({required this.trade, required this.onClose});

  @override
  Widget build(BuildContext context) {
    final color = trade.direction == 'LONG' ? longColor : shortColor;
    final dt = DateFormat('dd.MM HH:mm').format(trade.openedAt.toLocal());
    final pnlR = trade.pnlR;

    return Card(
      child: ListTile(
        leading: Icon(
          trade.direction == 'LONG' ? Icons.trending_up : Icons.trending_down,
          color: color,
        ),
        title: Text('${trade.symbol} • вход ${trade.entryPrice}'),
        subtitle: Text(trade.isOpen
            ? 'Открыта • $dt'
            : 'Закрыта по ${trade.exitPrice} • $dt'),
        trailing: trade.isOpen
            ? TextButton(onPressed: onClose, child: const Text('Закрыть'))
            : Text(
                pnlR == null
                    ? '—'
                    : '${pnlR >= 0 ? '+' : ''}${pnlR.toStringAsFixed(2)}R',
                style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.bold,
                    color: (pnlR ?? 0) >= 0 ? longColor : shortColor),
              ),
      ),
    );
  }
}

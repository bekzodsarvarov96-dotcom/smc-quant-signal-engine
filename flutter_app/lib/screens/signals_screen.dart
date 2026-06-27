import 'dart:async';

import 'package:flutter/material.dart';

import '../models/signal.dart';
import '../services/api_service.dart';
import '../widgets/signal_card.dart';
import 'signal_detail_screen.dart';

class SignalsScreen extends StatefulWidget {
  const SignalsScreen({super.key});

  @override
  State<SignalsScreen> createState() => _SignalsScreenState();
}

class _SignalsScreenState extends State<SignalsScreen> {
  final _api = ApiService();
  List<Signal> _signals = [];
  bool _loading = true;
  String? _error;
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _load();
    _refreshTimer =
        Timer.periodic(const Duration(seconds: 30), (_) => _load(silent: true));
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) setState(() => _loading = true);
    try {
      final signals = await _api.fetchSignals();
      if (!mounted) return;
      setState(() {
        _signals = signals;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Сигналы'),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _load),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _buildBody(),
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 120),
        Icon(Icons.cloud_off, size: 48, color: Colors.grey.shade600),
        const SizedBox(height: 12),
        Center(
            child: Text('Нет связи с сервером\n$_error',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.grey.shade500))),
      ]);
    }
    if (_signals.isEmpty) {
      return ListView(children: const [
        SizedBox(height: 140),
        Center(child: Text('Пока нет сигналов — сканер работает')),
      ]);
    }
    return ListView.builder(
      itemCount: _signals.length,
      itemBuilder: (_, i) => SignalCard(
        signal: _signals[i],
        onTap: () => Navigator.push(
          context,
          MaterialPageRoute(
              builder: (_) => SignalDetailScreen(signal: _signals[i])),
        ),
      ),
    );
  }
}

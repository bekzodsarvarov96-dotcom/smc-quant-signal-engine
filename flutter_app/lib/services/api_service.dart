import 'dart:convert';
import 'package:http/http.dart' as http;

import '../config.dart';
import '../models/signal.dart';
import '../models/trade.dart';

class ApiService {
  final String base = AppConfig.apiBaseUrl;

  Future<List<Signal>> fetchSignals({String? status}) async {
    final uri = Uri.parse('$base/api/v1/signals')
        .replace(queryParameters: {if (status != null) 'status': status});
    final r = await http.get(uri).timeout(const Duration(seconds: 15));
    _check(r);
    return (jsonDecode(r.body) as List)
        .map((e) => Signal.fromJson(e))
        .toList();
  }

  Future<List<Trade>> fetchTrades() async {
    final r = await http
        .get(Uri.parse('$base/api/v1/trades'))
        .timeout(const Duration(seconds: 15));
    _check(r);
    return (jsonDecode(r.body) as List).map((e) => Trade.fromJson(e)).toList();
  }

  Future<Stats> fetchStats() async {
    final r = await http
        .get(Uri.parse('$base/api/v1/stats'))
        .timeout(const Duration(seconds: 15));
    _check(r);
    return Stats.fromJson(jsonDecode(r.body));
  }

  Future<Trade> openTradeFromSignal(Signal s) async {
    final r = await http.post(
      Uri.parse('$base/api/v1/trades'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'signal_id': s.id,
        'symbol': s.symbol,
        'direction': s.direction,
        'entry_price': s.entry,
        'stop_loss': s.stopLoss,
        'take_profit': s.tp2,
      }),
    ).timeout(const Duration(seconds: 15));
    _check(r);
    return Trade.fromJson(jsonDecode(r.body));
  }

  Future<Trade> closeTrade(int id, double exitPrice) async {
    final r = await http.post(
      Uri.parse('$base/api/v1/trades/$id/close'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'exit_price': exitPrice}),
    ).timeout(const Duration(seconds: 15));
    _check(r);
    return Trade.fromJson(jsonDecode(r.body));
  }

  Future<BacktestResult> runBacktest({
    required String symbol,
    required String timeframe,
    required int days,
  }) async {
    final r = await http.post(
      Uri.parse('$base/api/v1/backtest'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'symbol': symbol, 'timeframe': timeframe, 'days': days}),
    ).timeout(const Duration(minutes: 5));
    _check(r);
    return BacktestResult.fromJson(jsonDecode(r.body));
  }

  void _check(http.Response r) {
    if (r.statusCode >= 400) {
      throw ApiException('HTTP ${r.statusCode}: ${r.body}');
    }
  }
}

class ApiException implements Exception {
  final String message;
  ApiException(this.message);
  @override
  String toString() => message;
}

import 'package:flutter/material.dart';

import 'screens/signals_screen.dart';
import 'screens/journal_screen.dart';
import 'screens/backtest_screen.dart';

void main() => runApp(const CryptoSignalsApp());

class CryptoSignalsApp extends StatelessWidget {
  const CryptoSignalsApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Crypto Signals',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF26A69A),
          brightness: Brightness.dark,
        ),
        scaffoldBackgroundColor: const Color(0xFF0E1116),
        cardTheme: const CardThemeData(
          color: Color(0xFF161B22),
          elevation: 0,
          margin: EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        ),
      ),
      home: const HomeShell(),
    );
  }
}

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  static const _screens = [
    SignalsScreen(),
    JournalScreen(),
    BacktestScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: _screens[_index],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.bolt), label: 'Сигналы'),
          NavigationDestination(icon: Icon(Icons.book), label: 'Журнал'),
          NavigationDestination(icon: Icon(Icons.science), label: 'Бэктест'),
        ],
      ),
    );
  }
}

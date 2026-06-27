/// Конфигурация клиента.
/// Для эмулятора Android: http://10.0.2.2:8000
/// Для реального устройства укажите IP машины с backend.
class AppConfig {
  static const String apiBaseUrl =
      String.fromEnvironment('API_URL', defaultValue: 'http://10.0.2.2:8000');
}

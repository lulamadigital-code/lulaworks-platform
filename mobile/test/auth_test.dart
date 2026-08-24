import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:lulaworks_mobile/api/api_client.dart';
import 'package:lulaworks_mobile/api/auth_errors.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('AuthErrorMapper', () {
    test('login 400/401 → wrong credentials, never raw JSON', () {
      for (final code in [400, 401]) {
        final msg = authErrorMessage(ApiException(code, '{"detail":"x"}'),
            context: AuthErrorContext.login);
        expect(msg, 'Email or password is incorrect.');
      }
    });

    test('general 401 → session expired', () {
      expect(authErrorMessage(ApiException(401, 'x')),
          contains('session has expired'));
    });

    test('403 / 429 / 500 map to clean lines', () {
      expect(authErrorMessage(ApiException(403, 'x')), contains("doesn't have access"));
      expect(authErrorMessage(ApiException(429, 'x')), contains('Too many attempts'));
      expect(authErrorMessage(ApiException(503, 'x')), contains('temporarily unavailable'));
    });

    test('connectivity + timeout classified (not generic)', () {
      expect(authErrorMessage(const SocketException('no route')),
          contains('Unable to connect'));
      expect(authErrorMessage(TimeoutException('slow')), contains('timed out'));
    });

    test('only a truly unknown error is generic', () {
      expect(authErrorMessage(Exception('???')), 'Something went wrong. Please try again.');
    });
  });

  group('Logout is always local + offline-safe', () {
    test('clears tokens even when the server is unreachable', () async {
      // Tokens present, pointing at a port that refuses connections.
      SharedPreferences.setMockInitialValues({
        'access': 'access-token',
        'refresh': 'refresh-token',
        'origin': 'http://127.0.0.1:1',
        'me': '{"role":"Worker","permissions":["work.edit"]}',
      });
      final api = await ApiClient.create();
      expect(api.isAuthenticated, isTrue);

      // Best-effort server revoke will fail — logout must still succeed locally.
      await api.logout();

      expect(api.isAuthenticated, isFalse);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('access'), isNull);
      expect(prefs.getString('refresh'), isNull);
      expect(prefs.getString('me'), isNull);
    });
  });
}

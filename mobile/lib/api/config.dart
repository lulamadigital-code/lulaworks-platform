// Where the LulaWorks backend lives.
//
// The default suits the platform per host:
//  - Android emulator reaches the host machine on 10.0.2.2
//  - iOS simulator / Flutter web reach it on localhost
// The base URL is also editable on the login screen, so a real device can point
// at a LAN IP or a deployed environment without a rebuild.
import 'package:flutter/foundation.dart';

class ApiConfig {
  static const String pathPrefix = '/api/v1';

  /// Sensible default backend origin for the current platform.
  static String get defaultOrigin {
    if (kIsWeb) return 'http://localhost:8000';
    if (defaultTargetPlatform == TargetPlatform.android) {
      return 'http://10.0.2.2:8000';
    }
    return 'http://localhost:8000';
  }
}

// Where the Lulaworks backend lives.
//
// The default is `localhost:8000` on every platform. For a device or emulator
// that means the *device's* localhost, so a local backend is reached by
// tunnelling it in with `adb reverse tcp:8000 tcp:8000` (Android) — no LAN IP or
// ALLOWED_HOSTS change needed, since the Host header stays `localhost`.
// Without a reverse tunnel, point the (editable) login Server field at the
// host: the Android emulator alias `http://10.0.2.2:8000`, or a LAN IP such as
// `http://192.168.x.x:8000`, or a deployed environment — no rebuild required.

class ApiConfig {
  static const String pathPrefix = '/api/v1';

  /// Production is the default — the app talks to the live backend over the
  /// internet, no tunnel needed. For local development, override the Server
  /// field on the login screen with `http://localhost:8000` (reached via
  /// `adb reverse tcp:8000 tcp:8000`).
  static String get defaultOrigin => 'https://www.lulaworks.com';
}

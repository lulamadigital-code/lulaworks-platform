import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'config.dart';

/// Raised for a non-2xx response. Carries the backend's error envelope
/// ({"error": {code, message, detail}}) so screens can show a real message.
class ApiException implements Exception {
  ApiException(this.statusCode, this.message, {this.code});
  final int statusCode;
  final String message;
  final String? code;

  bool get isAuth => statusCode == 401;
  bool get isForbidden => statusCode == 403; // e.g. Golden-Rule / RBAC gate
  @override
  String toString() => message;
}

/// Thin JSON-over-HTTP client for the LulaWorks API.
///
/// Handles JWT bearer auth, one-shot access-token refresh on 401, persistence of
/// the token pair + backend origin, and parsing of the standard error envelope.
class ApiClient {
  ApiClient._(this._prefs);

  final SharedPreferences _prefs;
  String? _access;
  String? _refresh;
  late String _origin;

  static Future<ApiClient> create() async {
    final prefs = await SharedPreferences.getInstance();
    final client = ApiClient._(prefs);
    client._origin = prefs.getString('origin') ?? ApiConfig.defaultOrigin;
    client._access = prefs.getString('access');
    client._refresh = prefs.getString('refresh');
    return client;
  }

  String get origin => _origin;
  bool get isAuthenticated => _access != null;

  Future<void> setOrigin(String origin) async {
    _origin = origin.trim().replaceAll(RegExp(r'/+$'), '');
    await _prefs.setString('origin', _origin);
  }

  Uri _uri(String path) => Uri.parse('$_origin${ApiConfig.pathPrefix}$path');

  Map<String, String> _headers({bool auth = true}) => {
        'Content-Type': 'application/json',
        if (auth && _access != null) 'Authorization': 'Bearer $_access',
      };

  // ── Auth ────────────────────────────────────────────────────────────────
  Future<void> login(String email, String password) async {
    final resp = await http.post(
      _uri('/auth/token/'),
      headers: _headers(auth: false),
      body: jsonEncode({'email': email, 'password': password}),
    );
    if (resp.statusCode != 200) {
      throw ApiException(resp.statusCode, 'Login failed — check your credentials.');
    }
    final data = _decode(resp) as Map<String, dynamic>;
    _access = data['access'] as String;
    _refresh = data['refresh'] as String?;
    await _prefs.setString('access', _access!);
    if (_refresh != null) await _prefs.setString('refresh', _refresh!);
  }

  Future<void> logout() async {
    _access = null;
    _refresh = null;
    await _prefs.remove('access');
    await _prefs.remove('refresh');
  }

  Future<bool> _tryRefresh() async {
    if (_refresh == null) return false;
    final resp = await http.post(
      _uri('/auth/token/refresh/'),
      headers: _headers(auth: false),
      body: jsonEncode({'refresh': _refresh}),
    );
    if (resp.statusCode != 200) return false;
    _access = (_decode(resp) as Map<String, dynamic>)['access'] as String;
    await _prefs.setString('access', _access!);
    return true;
  }

  // ── Requests ──────────────────────────────────────────────────────────────
  Future<dynamic> get(String path) => _send('GET', path);
  Future<dynamic> post(String path, [Map<String, dynamic>? body]) =>
      _send('POST', path, body);

  /// Multipart POST — for uploading a receipt/invoice/photo from the field.
  /// Sends optional string [fields] plus one file at [filePath].
  Future<dynamic> postMultipart(String path,
      {Map<String, String> fields = const {},
      String? filePath,
      String fileField = 'file',
      bool retry = true}) async {
    final req = http.MultipartRequest('POST', _uri(path));
    if (_access != null) req.headers['Authorization'] = 'Bearer $_access';
    req.fields.addAll(fields);
    if (filePath != null) {
      req.files.add(await http.MultipartFile.fromPath(fileField, filePath));
    }
    final resp = await http.Response.fromStream(await req.send());
    if (resp.statusCode == 401 && retry && await _tryRefresh()) {
      return postMultipart(path,
          fields: fields, filePath: filePath, fileField: fileField, retry: false);
    }
    if (resp.statusCode >= 200 && resp.statusCode < 300) {
      return resp.bodyBytes.isEmpty ? null : _decode(resp);
    }
    throw _error(resp);
  }

  Future<dynamic> _send(String method, String path,
      [Map<String, dynamic>? body, bool retry = true]) async {
    final uri = _uri(path);
    final encoded = body == null ? null : jsonEncode(body);
    http.Response resp;
    if (method == 'POST') {
      resp = await http.post(uri, headers: _headers(), body: encoded);
    } else {
      resp = await http.get(uri, headers: _headers());
    }

    if (resp.statusCode == 401 && retry && await _tryRefresh()) {
      return _send(method, path, body, false);
    }
    if (resp.statusCode >= 200 && resp.statusCode < 300) {
      return resp.bodyBytes.isEmpty ? null : _decode(resp);
    }
    throw _error(resp);
  }

  /// Decode a JSON body as UTF-8 explicitly. The `http` package falls back to
  /// Latin-1 when the response has no charset (DRF omits it), which mangles
  /// non-ASCII text (em-dashes, accented names). Always decode the raw bytes.
  dynamic _decode(http.Response resp) => jsonDecode(utf8.decode(resp.bodyBytes));

  ApiException _error(http.Response resp) {
    String message = 'Request failed (${resp.statusCode}).';
    String? code;
    try {
      final data = jsonDecode(utf8.decode(resp.bodyBytes));
      if (data is Map && data['error'] is Map) {
        message = data['error']['message']?.toString() ?? message;
        code = data['error']['code']?.toString();
      } else if (data is Map && data['detail'] != null) {
        message = data['detail'].toString();
      }
    } catch (_) {/* keep default message */}
    return ApiException(resp.statusCode, message, code: code);
  }
}

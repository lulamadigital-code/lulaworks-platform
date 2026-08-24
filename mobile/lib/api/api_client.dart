import 'dart:convert';
import 'dart:typed_data';

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

  /// Called when a request gets 401 and the refresh token can't renew it — the
  /// session is dead. The app wires this to force a sign-out → login screen, so
  /// an expired token never traps the user on an error page.
  void Function()? onAuthFailure;

  // Cached identity from /me/ — the resolved role and permission codenames that
  // drive which surfaces the app shows. Persisted so a returning user sees the
  // right app before the network round-trip completes.
  Map<String, dynamic> _me = const {};
  Set<String> _perms = {};
  String? _role;

  static Future<ApiClient> create() async {
    final prefs = await SharedPreferences.getInstance();
    final client = ApiClient._(prefs);
    client._origin = prefs.getString('origin') ?? ApiConfig.defaultOrigin;
    client._access = prefs.getString('access');
    client._refresh = prefs.getString('refresh');
    final meStr = prefs.getString('me');
    if (meStr != null) {
      try {
        client._applyMe(jsonDecode(meStr));
      } catch (_) {/* ignore a corrupt cache */}
    }
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
    final resp = await http
        .post(_uri('/auth/token/'),
            headers: _headers(auth: false),
            body: jsonEncode({'email': email, 'password': password}))
        .timeout(_timeout);
    if (resp.statusCode != 200) {
      throw ApiException(resp.statusCode, 'Login failed — check your credentials.');
    }
    final data = _decode(resp) as Map<String, dynamic>;
    _access = data['access'] as String;
    _refresh = data['refresh'] as String?;
    await _prefs.setString('access', _access!);
    if (_refresh != null) await _prefs.setString('refresh', _refresh!);
    try {
      await refreshMe();               // resolve role + permissions up front
    } catch (_) {/* non-fatal: screens re-fetch /me/ anyway */}
  }

  /// Sign out. This ALWAYS clears local authentication state — it can never
  /// fail or leave the user trapped in the app. The server-side token revoke is
  /// strictly best-effort: if it's slow, offline, or errors, we ignore it and
  /// still sign out locally (JWT is stateless — a dropped revoke only means the
  /// old refresh token lives out its short TTL server-side).
  Future<void> logout() async {
    final refresh = _refresh;

    // 1) Best-effort server revoke (blacklist the refresh token). Never awaited
    //    in a way that can block sign-out or throw.
    if (refresh != null) {
      try {
        await http
            .post(_uri('/auth/logout/'),
                headers: _headers(auth: false),
                body: jsonEncode({'refresh': refresh}))
            .timeout(const Duration(seconds: 3));
      } catch (_) {/* offline / server down / timeout — sign out locally anyway */}
    }

    // 2) Clear ALL in-memory auth + identity state.
    _access = null;
    _refresh = null;
    _me = const {};
    _perms = {};
    _role = null;

    // 3) Clear persisted sensitive state.
    await _prefs.remove('access');
    await _prefs.remove('refresh');
    await _prefs.remove('me');

    // Return to the default (production) server on sign-out, so a stale dev
    // origin doesn't linger; the login screen still lets you change it.
    _origin = ApiConfig.defaultOrigin;
    await _prefs.setString('origin', _origin);
  }

  // ── Identity & permissions ──────────────────────────────────────────────────
  /// Fetch and cache /me/ (role + permission codenames + active company).
  Future<void> refreshMe() async {
    final data = await get('/me/');
    if (data is Map) {
      _applyMe(data);
      await _prefs.setString('me', jsonEncode(data));
    }
  }

  void _applyMe(dynamic data) {
    _me = (data as Map).cast<String, dynamic>();
    _perms =
        (((_me['permissions'] as List?) ?? const []).map((e) => '$e').toSet());
    _role = _me['role']?.toString();
  }

  Map<String, dynamic> get me => _me;
  String? get role => _role;

  /// True if the signed-in user holds [code] (e.g. 'finance.view_money'). The
  /// backend still enforces every action; this only decides what the UI offers.
  bool can(String code) => _perms.contains(code);

  String get firstName => ((_me['user'] as Map?)?['first_name'] ?? '').toString();
  String get companyName =>
      ((_me['active_company'] as Map?)?['name'] ?? '').toString();

  static const _currencySymbols = {
    'ZAR': 'R', 'USD': '\$', 'EUR': '€', 'GBP': '£', 'AUD': 'A\$',
  };

  /// The active company's currency symbol (falls back to the code, then 'R').
  String get currencySymbol {
    final code =
        (_me['active_company'] as Map?)?['currency']?.toString() ?? 'ZAR';
    return _currencySymbols[code] ?? code;
  }

  /// Format a money value in the company currency, with thousands spacing.
  /// Returns '—' for null (a money field the backend withheld under the Golden
  /// Rule), so screens can call this unconditionally.
  String money(dynamic v) {
    if (v == null) return '—';
    final n = double.tryParse('$v');
    if (n == null) return '$v';
    final parts = n.toStringAsFixed(2).split('.');
    final whole = parts[0]
        .replaceAllMapped(RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]} ');
    return '$currencySymbol $whole.${parts[1]}';
  }

  // Named capability checks used across the UI.
  bool get canViewMoney => can('finance.view_money');
  bool get canManageCompany => can('company.manage');
  bool get canInviteUsers => can('users.invite');
  bool get canGenerateAi => can('ai.generate');
  bool get canManageCompliance => can('compliance.manage');
  bool get canOverrideCompliance => can('compliance.override');
  // Field execution — starting/completing a task and filing task reports.
  bool get canManageExecution => can('execution.manage');
  // Doing the field work itself (start/complete/report/tick checklist). A
  // groundfloor worker holds work.edit, not the execution.manage umbrella.
  bool get canExecuteWork => can('work.edit') || can('execution.manage');
  String get userId => ((_me['user'] as Map?)?['id'] ?? '').toString();
  bool get canManageCustomers => can('customers.manage');
  // Who should even see the customer database — anyone doing commercial work,
  // not pure field crew. Reads are open on the backend; this just scopes the UI.
  bool get canSeeCustomers =>
      can('customers.manage') ||
      can('crm.manage') ||
      can('quotes.create') ||
      can('projects.create');
  // Procurement / commercial modules (Phase 4).
  bool get canProcurement => can('procurement.manage');
  bool get canApprovePO => can('po.approve');
  bool get canSeeRfq => can('rfq.upload') || can('rfq.approve');
  bool get canApproveRfq => can('rfq.approve');
  bool get canSeeEstimates =>
      can('estimating.manage') || can('estimating.approve');
  bool get canApproveEstimate => can('estimating.approve');
  // Quotations (Phase 6).
  bool get canCreateQuote => can('quotes.create');
  bool get canApproveQuote => can('quotes.approve');
  bool get canDownloadPdf => can('quotes.download');
  // Quote visibility requires an actual quote permission — NOT mere
  // projects.view. A field worker who can see their jobs must not thereby see
  // the quotation pipeline (Golden Rule: quote totals are money).
  bool get canSeeQuotes =>
      can('quotes.create') ||
      can('quotes.approve') ||
      can('quotes.download');
  // Commercial documents — tax invoices & delivery notes (Phase 7).
  bool get canSeeCommercial =>
      can('finance.view_money') ||
      can('invoices.approve') ||
      can('quotes.download') ||
      can('quotes.create');
  bool get canTransitionCommercial =>
      can('invoices.approve') || can('quotes.approve');
  bool get canRecordPayment => can('finance.manage') || can('invoices.approve');

  /// Network-request timeout — a hung socket becomes a clean TimeoutException
  /// (mapped to a friendly message) instead of spinning forever.
  static const _timeout = Duration(seconds: 20);

  /// One-shot access-token refresh. Returns false (never throws) so a transient
  /// network error during refresh routes to a clean sign-out, not a crash — and
  /// callers pass retry=false on the retried request, so there is no loop.
  Future<bool> _tryRefresh() async {
    if (_refresh == null) return false;
    try {
      final resp = await http
          .post(_uri('/auth/token/refresh/'),
              headers: _headers(auth: false),
              body: jsonEncode({'refresh': _refresh}))
          .timeout(_timeout);
      if (resp.statusCode != 200) return false;
      _access = (_decode(resp) as Map<String, dynamic>)['access'] as String;
      await _prefs.setString('access', _access!);
      return true;
    } catch (_) {
      return false;
    }
  }

  // ── Requests ──────────────────────────────────────────────────────────────
  Future<dynamic> get(String path) => _send('GET', path);
  Future<dynamic> post(String path, [Map<String, dynamic>? body]) =>
      _send('POST', path, body);
  Future<dynamic> patch(String path, [Map<String, dynamic>? body]) =>
      _send('PATCH', path, body);
  Future<dynamic> delete(String path) => _send('DELETE', path);

  /// Fetch raw bytes (e.g. a generated PDF) with auth + one-shot token refresh.
  Future<Uint8List> getBytes(String path, {bool retry = true}) async {
    final resp = await http.get(_uri(path), headers: _headers());
    if (resp.statusCode == 401 && retry && await _tryRefresh()) {
      return getBytes(path, retry: false);
    }
    if (resp.statusCode >= 200 && resp.statusCode < 300) return resp.bodyBytes;
    throw _error(resp);
  }

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
    final headers = _headers();
    http.Response resp;
    switch (method) {
      case 'POST':
        resp = await http.post(uri, headers: headers, body: encoded).timeout(_timeout);
      case 'PATCH':
        resp = await http.patch(uri, headers: headers, body: encoded).timeout(_timeout);
      case 'PUT':
        resp = await http.put(uri, headers: headers, body: encoded).timeout(_timeout);
      case 'DELETE':
        resp = await http.delete(uri, headers: headers).timeout(_timeout);
      default:
        resp = await http.get(uri, headers: headers).timeout(_timeout);
    }

    if (resp.statusCode == 401 && retry) {
      if (await _tryRefresh()) return _send(method, path, body, false);
      onAuthFailure?.call(); // refresh failed → the session is dead
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

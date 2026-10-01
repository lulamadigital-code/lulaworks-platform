import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';

import 'api_client.dart';
import 'attendance_store.dart';
import 'chat_store.dart';
import 'report_store.dart';

/// One coordinator over the per-feature offline outboxes (field reports,
/// attendance, task chat). It is the single place that knows "is there unsynced
/// field work, and are we online" and the single trigger that flushes
/// everything when the network returns.
///
/// It does NOT replace the per-screen flush calls (those still run when a given
/// screen opens); it adds app-wide coordination: a live pending-count + online
/// flag for the UI, an auto-flush on reconnect, and a manual retry. Each store
/// keeps its own capture-time stamps and "network error keeps / 4xx drops"
/// semantics — this just drives them together.
class SyncService {
  SyncService(this.api);
  final ApiClient api;

  /// The running coordinator, if any — so a screen that just queued something
  /// offline can bump the count (`SyncService.instance?.refresh()`) without
  /// threading the service through the widget tree.
  static SyncService? instance;

  final _reports = ReportStore();
  final _attendance = AttendanceStore();
  final _chat = ChatStore();

  /// Total items waiting to sync, across all outboxes. UI binds to this.
  final ValueNotifier<int> pending = ValueNotifier<int>(0);

  /// Whether the device currently has a network path. Best-effort — the real
  /// proof is a request succeeding, so flush is always attempted on regain.
  final ValueNotifier<bool> online = ValueNotifier<bool>(true);

  StreamSubscription<ConnectivityResult>? _sub;
  bool _flushing = false;

  /// Start listening. Safe to call once (e.g. from the app shell's initState).
  Future<void> start() async {
    instance = this;
    await refresh();
    try {
      final initial = await Connectivity().checkConnectivity();
      online.value = initial != ConnectivityResult.none;
    } catch (_) {/* assume online; a failed request will correct it */}
    _sub = Connectivity().onConnectivityChanged.listen((result) {
      final isOnline = result != ConnectivityResult.none;
      online.value = isOnline;
      if (isOnline) flushAll(); // back online → push whatever is queued
    });
    // If we start already online with a backlog, clear it.
    if (online.value && pending.value > 0) {
      unawaited(flushAll());
    }
  }

  void dispose() {
    _sub?.cancel();
    if (identical(instance, this)) instance = null;
  }

  /// Recompute the pending total from every outbox.
  Future<void> refresh() async {
    try {
      final counts = await Future.wait([
        _reports.pendingCount(),
        _attendance.pendingCount(),
        _chat.totalPending(),
      ]);
      pending.value = counts.fold(0, (a, b) => a + b);
    } catch (_) {/* leave the last known count */}
  }

  /// Attempt to flush every outbox. Returns how many items were sent. Reentrancy-
  /// guarded so a reconnect burst + a manual retry can't double-send.
  Future<int> flushAll() async {
    if (_flushing) return 0;
    _flushing = true;
    var sent = 0;
    try {
      sent += await _reports.flush(api);
      sent += await _attendance.flush(api);
      sent += await _chat.flushAll(api);
      // A successful flush means we reached the server.
      if (sent > 0) online.value = true;
    } catch (_) {/* stores already keep what didn't send */} finally {
      _flushing = false;
      await refresh();
    }
    return sent;
  }
}

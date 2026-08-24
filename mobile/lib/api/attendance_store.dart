import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'api_client.dart';

/// Offline queue for attendance events. Field sites often have no signal, so a
/// clock/break event is captured with its real device time (occurred_at) and
/// held locally until the network returns — it is never lost, and never
/// re-timestamped on sync (the server keeps occurred_at).
class AttendanceStore {
  static const _key = 'attendance_pending';

  Future<List<Map<String, dynamic>>> pending() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_key);
    if (raw == null || raw.isEmpty) return [];
    try {
      return (jsonDecode(raw) as List).cast<Map<String, dynamic>>();
    } catch (_) {
      return [];
    }
  }

  Future<int> pendingCount() async => (await pending()).length;

  Future<void> _save(List<Map<String, dynamic>> items) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key, jsonEncode(items));
  }

  /// Queue an event to sync later (offline). [body] is the POST payload.
  Future<void> enqueue(Map<String, dynamic> body) async {
    final items = await pending();
    items.add(body);
    await _save(items);
  }

  /// Try to POST every queued event. Returns how many synced. A queued event is
  /// dropped only when the server actually accepts OR permanently rejects it
  /// (4xx) — a network failure keeps it for the next attempt (no data loss, no
  /// infinite retry on a bad row).
  Future<int> flush(ApiClient api) async {
    final items = await pending();
    if (items.isEmpty) return 0;
    final remaining = <Map<String, dynamic>>[];
    var synced = 0;
    for (final body in items) {
      try {
        await api.post('/attendance-events/', body);
        synced++;
      } on ApiException {
        // Server rejected it (malformed / not allowed) — drop, don't loop.
        synced++;
      } catch (_) {
        remaining.add(body); // network — keep for next time
      }
    }
    await _save(remaining);
    return synced;
  }
}

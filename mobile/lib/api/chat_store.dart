import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'api_client.dart';

/// Offline outbox for task-chat text messages. A message that can't send (no
/// signal) is held locally and flushed when the network returns — never lost.
/// Keyed per task so each thread flushes independently.
class ChatStore {
  static const _key = 'chat_outbox';

  Future<Map<String, dynamic>> _all() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_key);
    if (raw == null || raw.isEmpty) return {};
    try {
      return (jsonDecode(raw) as Map).cast<String, dynamic>();
    } catch (_) {
      return {};
    }
  }

  Future<void> _save(Map<String, dynamic> all) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key, jsonEncode(all));
  }

  /// The pending message bodies for a task, oldest first.
  Future<List<String>> pending(String taskId) async {
    final all = await _all();
    return ((all[taskId] as List?) ?? const []).map((e) => '$e').toList();
  }

  Future<void> enqueue(String taskId, String body) async {
    final all = await _all();
    final list = ((all[taskId] as List?) ?? []).map((e) => '$e').toList()..add(body);
    all[taskId] = list;
    await _save(all);
  }

  /// Try to send every queued message for [taskId]. Returns how many sent.
  /// A permanent rejection (4xx) is dropped; a network error is kept.
  Future<int> flush(ApiClient api, String taskId) async {
    final list = await pending(taskId);
    if (list.isEmpty) return 0;
    final remaining = <String>[];
    var sent = 0;
    for (final body in list) {
      try {
        await api.post('/task-messages/', {'task': taskId, 'body': body});
        sent++;
      } on ApiException {
        sent++; // server rejected — don't loop forever
      } catch (_) {
        remaining.add(body); // still offline
      }
    }
    final all = await _all();
    if (remaining.isEmpty) {
      all.remove(taskId);
    } else {
      all[taskId] = remaining;
    }
    await _save(all);
    return sent;
  }
}

import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_client.dart';

/// Offline outbox for field reports (and their photos). A report captured with
/// no signal is stored locally — body + a persisted copy of the photo — and
/// synced when the network returns. The GPS/time captured at the moment stays
/// with it; nothing is re-stamped, and no report or photo is lost.
class ReportStore {
  static const _key = 'report_outbox';

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

  /// Queue a report to sync later. [body] is the POST payload; [photoPath], if
  /// given, is copied into app storage so it survives until sync.
  Future<void> enqueue(Map<String, dynamic> body, {String? photoPath}) async {
    String? stored;
    if (photoPath != null) {
      try {
        final dir = await getApplicationDocumentsDirectory();
        final ext = photoPath.contains('.') ? photoPath.split('.').last : 'jpg';
        final dest = '${dir.path}/report_${DateTime.now().microsecondsSinceEpoch}.$ext';
        await File(photoPath).copy(dest);
        stored = dest;
      } catch (_) {/* keep the report even if the photo copy fails */}
    }
    final items = await pending();
    items.add({'body': body, if (stored != null) 'photo': stored});
    await _save(items);
  }

  /// Try to sync every queued report. Returns how many were sent. A report is
  /// dropped when the server accepts OR permanently rejects it (4xx); a network
  /// error keeps it for next time. The persisted photo is deleted once sent.
  Future<int> flush(ApiClient api) async {
    final items = await pending();
    if (items.isEmpty) return 0;
    final remaining = <Map<String, dynamic>>[];
    var sent = 0;
    for (final item in items) {
      final body = (item['body'] as Map).cast<String, dynamic>();
      final photo = item['photo'] as String?;
      try {
        final report = await api.post('/task-reports/', body) as Map<String, dynamic>;
        if (photo != null && await File(photo).exists()) {
          try {
            await api.postMultipart('/task-reports/${report['id']}/photo/',
                filePath: photo);
          } catch (_) {/* report synced; a failed photo isn't worth re-queuing */}
          await _safeDelete(photo);
        }
        sent++;
      } on ApiException {
        if (photo != null) await _safeDelete(photo);
        sent++; // permanent rejection — don't loop
      } catch (_) {
        remaining.add(item); // still offline
      }
    }
    await _save(remaining);
    return sent;
  }

  Future<void> _safeDelete(String path) async {
    try {
      final f = File(path);
      if (await f.exists()) await f.delete();
    } catch (_) {/* ignore */}
  }
}

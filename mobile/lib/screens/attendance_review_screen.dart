import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';

/// Manager review of attendance corrections. A worker can request a correction
/// (a missed clock-in/out) but never self-approve; here a manager sees the
/// pending queue and approves or rejects each. Backed by
/// /attendance-events/?pending=1 and a PATCH of status.
class AttendanceReviewScreen extends StatefulWidget {
  const AttendanceReviewScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<AttendanceReviewScreen> createState() => _AttendanceReviewScreenState();
}

class _AttendanceReviewScreenState extends State<AttendanceReviewScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();
  final _acting = <String>{};

  Future<List<Map<String, dynamic>>> _load() async {
    final r = await widget.api.get('/attendance-events/?pending=1');
    if (r is Map && r['results'] is List) {
      return (r['results'] as List).cast<Map<String, dynamic>>();
    }
    if (r is List) return r.cast<Map<String, dynamic>>();
    return [];
  }

  void _reload() => setState(() { _future = _load(); });

  Future<void> _review(Map<String, dynamic> e, String status) async {
    final id = '${e['id']}';
    setState(() => _acting.add(id));
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.patch('/attendance-events/$id/', {'status': status});
      messenger.showSnackBar(SnackBar(
          content: Text(status == 'approved' ? 'Correction approved' : 'Correction rejected')));
      _reload();
    } on ApiException catch (ex) {
      messenger.showSnackBar(SnackBar(content: Text(ex.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    } finally {
      if (mounted) setState(() => _acting.remove(id));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
          title: const Text('Attendance review'), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => _reload(),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator(color: kBrand));
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 120),
                const Icon(Icons.cloud_off, size: 44, color: kMuted),
                const SizedBox(height: 12),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
              ]);
            }
            final items = snap.data ?? const [];
            if (items.isEmpty) {
              return ListView(children: const [
                SizedBox(height: 140),
                Icon(Icons.verified_outlined, size: 48, color: kMuted),
                SizedBox(height: 12),
                Center(child: Text('Nothing to review',
                    style: TextStyle(fontSize: 15.5, fontWeight: FontWeight.w600, color: kInk))),
                SizedBox(height: 2),
                Center(child: Text('Attendance corrections will appear here.',
                    style: TextStyle(fontSize: 13, color: kMuted))),
              ]);
            }
            return ListView(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 28),
              children: [for (final e in items) _card(context, e)],
            );
          },
        ),
      ),
    );
  }

  Widget _card(BuildContext context, Map<String, dynamic> e) {
    final id = '${e['id']}';
    final busy = _acting.contains(id);
    final when = DateTime.tryParse('${e['occurred_at']}')?.toLocal();
    final note = '${e['note'] ?? ''}'.trim();
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: kLine)),
      padding: const EdgeInsets.all(16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Container(
            width: 40, height: 40,
            decoration: BoxDecoration(
                color: kBrandTint, borderRadius: BorderRadius.circular(11)),
            child: const Icon(Icons.edit_calendar_outlined, color: kBrandDark, size: 21),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${e['user_name'] ?? 'Worker'}',
                  style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: kInk)),
              Text('${e['kind_display'] ?? e['kind']}'
                  '${when == null ? '' : '  ·  ${_fmt(when)}'}',
                  style: const TextStyle(fontSize: 12.5, color: kMuted)),
            ]),
          ),
        ]),
        if (note.isNotEmpty) ...[
          const SizedBox(height: 12),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(11),
            decoration: BoxDecoration(
                color: kBg, borderRadius: BorderRadius.circular(10)),
            child: Text('"$note"',
                style: const TextStyle(fontSize: 13.5, color: kInk, height: 1.3)),
          ),
        ],
        const SizedBox(height: 14),
        Row(children: [
          Expanded(
            child: OutlinedButton.icon(
              onPressed: busy ? null : () => _review(e, 'rejected'),
              icon: const Icon(Icons.close, size: 18),
              label: const Text('Reject'),
              style: OutlinedButton.styleFrom(
                  foregroundColor: kRed, side: const BorderSide(color: kLine)),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: FilledButton.icon(
              onPressed: busy ? null : () => _review(e, 'approved'),
              icon: busy
                  ? const SizedBox(width: 16, height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Icon(Icons.check, size: 18),
              label: const Text('Approve'),
            ),
          ),
        ]),
      ]),
    );
  }

  String _fmt(DateTime t) =>
      '${t.day}/${t.month} ${t.hour.toString().padLeft(2, '0')}:'
      '${t.minute.toString().padLeft(2, '0')}';
}

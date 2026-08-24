import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

/// Field report detail + review loop (§15/§16/§36). Shows the captured record,
/// its review status and the review thread; a reviewer can Approve or Return
/// for correction (with a comment), and the author can Resubmit a returned
/// report. Everyone in the loop can add a comment.
class ReportDetailScreen extends StatefulWidget {
  const ReportDetailScreen(
      {super.key, required this.api, required this.report, required this.taskName});
  final ApiClient api;
  final Map<String, dynamic> report;
  final String taskName;

  @override
  State<ReportDetailScreen> createState() => _ReportDetailScreenState();
}

class _ReportDetailScreenState extends State<ReportDetailScreen> {
  late Map<String, dynamic> _r = widget.report;
  final _comment = TextEditingController();
  bool _busy = false;
  bool _changed = false;

  @override
  void dispose() {
    _comment.dispose();
    super.dispose();
  }

  Future<void> _act(String path, {Map<String, dynamic>? body}) async {
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final res = await widget.api.post('/task-reports/${_r['id']}/$path/', body);
      if (res is Map) {
        setState(() {
          _r = res.cast<String, dynamic>();
          _changed = true;
        });
      }
      _comment.clear();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _return() async {
    final c = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Return for correction'),
        content: LulaTextField(
            controller: c, label: 'What needs fixing?', maxLines: 3, required: true),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true), child: const Text('Return')),
        ],
      ),
    );
    if (ok == true && c.text.trim().isNotEmpty) {
      await _act('return', body: {'comment': c.text.trim()});
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = _r;
    final status = '${r['status']}';
    final isAuthor = '${r['employee_id']}' == widget.api.userId;
    final canReview = widget.api.canReviewReports;
    final comments = (r['comments'] as List? ?? const []).cast<Map<String, dynamic>>();
    final when = DateTime.tryParse('${r['reported_at']}')?.toLocal();
    final amount = r['amount'];
    final hasAmount = amount != null && '$amount' != '0.00' && '$amount' != '0';
    final items = (r['items'] as List? ?? const []).cast<Map<String, dynamic>>();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Report'),
        scrolledUnderElevation: 1,
        leading: BackButton(onPressed: () => Navigator.pop(context, _changed)),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 28),
        children: [
          Row(children: [
            Expanded(
              child: Text('${r['title']}',
                  style: const TextStyle(
                      fontSize: 19, fontWeight: FontWeight.w700, color: kInk)),
            ),
            _statusBadge(status),
          ]),
          const SizedBox(height: 2),
          Text('${r['kind_display'] ?? r['kind']}',
              style: const TextStyle(fontSize: 13, color: kMuted)),
          const SizedBox(height: 16),

          _row('Task', widget.taskName),
          if ('${r['employee_name'] ?? ''}'.isNotEmpty)
            _row('Submitted by', '${r['employee_name']}'),
          if (when != null) _row('Time', _fmt(when)),
          _row('Location', _locationText(r)),
          if ('${r['supplier'] ?? ''}'.isNotEmpty) _row('Supplier', '${r['supplier']}'),
          if (hasAmount) _row('Amount', 'R $amount'),
          if ('${r['reviewed_by_name'] ?? ''}'.isNotEmpty)
            _row('Reviewed by', '${r['reviewed_by_name']}'),

          if ('${r['notes'] ?? ''}'.isNotEmpty) ...[
            const SizedBox(height: 12),
            _label('NOTES'),
            const SizedBox(height: 4),
            Text('${r['notes']}', style: const TextStyle(fontSize: 14, color: kInk)),
          ],

          if (items.isNotEmpty) ...[
            const SizedBox(height: 14),
            _label('ITEMS'),
            const SizedBox(height: 6),
            for (final it in items)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: Row(children: [
                  Expanded(child: Text('${it['description'] ?? ''}',
                      style: const TextStyle(fontSize: 13.5, color: kInk))),
                  Text('${it['quantity'] ?? ''} ${it['unit'] ?? ''}',
                      style: const TextStyle(fontSize: 13, color: kMuted)),
                ]),
              ),
          ],

          // ── Review thread ──────────────────────────────────────────────
          const SizedBox(height: 22),
          _label('REVIEW'),
          const SizedBox(height: 8),
          if (comments.isEmpty)
            const Text('No review comments yet.',
                style: TextStyle(fontSize: 13, color: kMuted))
          else
            for (final c in comments) _commentTile(c),

          const SizedBox(height: 12),
          Row(children: [
            Expanded(
              child: LulaTextField(controller: _comment, label: '', hint: 'Add a comment…'),
            ),
            const SizedBox(width: 8),
            IconButton.filled(
              onPressed: _busy
                  ? null
                  : () {
                      if (_comment.text.trim().isNotEmpty) {
                        _act('comment', body: {'body': _comment.text.trim()});
                      }
                    },
              icon: const Icon(Icons.send, size: 20),
            ),
          ]),

          const SizedBox(height: 20),
          _actions(status, isAuthor, canReview),
        ],
      ),
    );
  }

  Widget _actions(String status, bool isAuthor, bool canReview) {
    // Reviewer, report awaiting review → Approve / Return.
    if (canReview && status == 'submitted') {
      return Row(children: [
        Expanded(
          child: OutlinedButton.icon(
            onPressed: _busy ? null : _return,
            icon: const Icon(Icons.undo, size: 18),
            label: const Text('Return'),
            style: OutlinedButton.styleFrom(
                foregroundColor: kOrange, side: const BorderSide(color: kLine)),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: FilledButton.icon(
            onPressed: _busy ? null : () => _act('approve'),
            icon: const Icon(Icons.check, size: 18),
            label: const Text('Approve'),
          ),
        ),
      ]);
    }
    // Author, returned → Resubmit.
    if (isAuthor && status == 'returned') {
      return SizedBox(
        width: double.infinity,
        height: 50,
        child: FilledButton.icon(
          onPressed: _busy ? null : () => _act('resubmit'),
          icon: const Icon(Icons.send, size: 18),
          label: const Text('Resubmit report'),
        ),
      );
    }
    return const SizedBox.shrink();
  }

  Widget _statusBadge(String status) {
    final (Color c, String label) = switch (status) {
      'approved' => (kGreen, 'Approved'),
      'returned' => (kOrange, 'Returned'),
      _ => (kInfo, 'Submitted'),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
          color: c.withOpacity(0.13), borderRadius: BorderRadius.circular(8)),
      child: Text(label,
          style: TextStyle(color: c, fontSize: 12, fontWeight: FontWeight.w700)),
    );
  }

  Widget _commentTile(Map<String, dynamic> c) {
    final when = DateTime.tryParse('${c['created_at']}')?.toLocal();
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text('${c['author_name'] ?? 'Someone'}',
              style: const TextStyle(
                  fontSize: 13, fontWeight: FontWeight.w700, color: kBrandDark)),
          const SizedBox(width: 8),
          if (when != null)
            Text(_fmt(when), style: const TextStyle(fontSize: 11.5, color: kMuted)),
        ]),
        const SizedBox(height: 2),
        Text('${c['body']}', style: const TextStyle(fontSize: 14, color: kInk)),
      ]),
    );
  }

  Widget _row(String k, String v) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(width: 100,
              child: Text(k, style: const TextStyle(fontSize: 12.5, color: kMuted))),
          Expanded(child: Text(v, style: const TextStyle(fontSize: 14, color: kInk))),
        ]),
      );

  /// Location is "captured" when the report itself has GPS — distance_m only
  /// exists when the TASK has an expected site to compare against, so a captured
  /// fix on a site-less task must not read as "Not captured".
  String _locationText(Map<String, dynamic> r) {
    final hasGps = r['latitude'] != null && r['longitude'] != null;
    if (!hasGps) return 'Not captured';
    if (r['location_flagged'] == true) return 'Captured · outside expected area';
    if (r['distance_m'] != null) return 'Verified · ${r['distance_m']} m from site';
    return 'Captured · ${r['latitude']}, ${r['longitude']}';
  }

  Widget _label(String s) => Text(s,
      style: const TextStyle(
          fontSize: 11, fontWeight: FontWeight.w700, letterSpacing: 0.6, color: kMuted));

  String _fmt(DateTime t) =>
      '${t.day}/${t.month}/${t.year} · '
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';

/// Attention Centre — cross-module exceptions that need a human, grouped by
/// severity. Read-only mirror of the web console; the backend detector is
/// permission-gated, so a person only sees what their role can access.
class AttentionScreen extends StatefulWidget {
  const AttentionScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<AttentionScreen> createState() => _AttentionScreenState();
}

class _AttentionScreenState extends State<AttentionScreen> {
  late Future<Map<String, dynamic>> _future = _load();

  Future<Map<String, dynamic>> _load() async {
    final body = await widget.api.get('/attention/');
    return (body as Map).cast<String, dynamic>();
  }

  Future<void> _refresh() async {
    setState(() => _future = _load());
    await _future;
  }

  static const _critical = Color(0xFFC0392B);
  static const _warning = Color(0xFF9A6A12);

  Color _color(String sev) => switch (sev) {
        'critical' => _critical,
        'warning' => _warning,
        _ => kGreen,
      };

  String _emoji(String sev) => switch (sev) {
        'critical' => '🔴',
        'warning' => '🟠',
        _ => '🟢',
      };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Attention Centre')),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: _refresh,
        child: FutureBuilder<Map<String, dynamic>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator(color: kBrand));
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 120),
                Center(child: Text(
                    snap.error is ApiException ? '${snap.error}'
                        : 'Could not load the Attention Centre.',
                    style: const TextStyle(color: kMuted))),
              ]);
            }
            final d = snap.data!;
            final counts = (d['counts'] as Map?)?.cast<String, dynamic>() ?? {};
            final items = <Map<String, dynamic>>[
              for (final k in ['critical', 'warning', 'info'])
                ...((d[k] as List?) ?? const [])
                    .map((e) => (e as Map).cast<String, dynamic>()),
            ];
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Row(children: [
                  _tile('🔴', '${counts['critical'] ?? 0}', 'Critical', _critical),
                  const SizedBox(width: 10),
                  _tile('🟠', '${counts['warning'] ?? 0}', 'Attention', _warning),
                  const SizedBox(width: 10),
                  _tile('🟢', '${counts['info'] ?? 0}', 'Info', kGreen),
                ]),
                const SizedBox(height: 16),
                if (items.isEmpty)
                  const Padding(
                    padding: EdgeInsets.only(top: 60),
                    child: Column(children: [
                      Text('✅', style: TextStyle(fontSize: 34)),
                      SizedBox(height: 8),
                      Text('All clear',
                          style: TextStyle(fontWeight: FontWeight.w700, color: kInk)),
                      SizedBox(height: 2),
                      Text('Nothing needs your attention right now.',
                          style: TextStyle(fontSize: 13, color: kMuted)),
                    ]),
                  )
                else
                  ...items.map(_row),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _tile(String emoji, String n, String label, Color c) => Expanded(
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 14),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: kLine),
          ),
          child: Column(children: [
            Text(emoji, style: const TextStyle(fontSize: 16)),
            const SizedBox(height: 4),
            Text(n, style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800, color: c)),
            Text(label, style: const TextStyle(fontSize: 11.5, color: kMuted)),
          ]),
        ),
      );

  Widget _row(Map<String, dynamic> it) {
    final sev = '${it['severity']}';
    final c = _color(sev);
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border(left: BorderSide(color: c, width: 3),
            top: const BorderSide(color: kLine), right: const BorderSide(color: kLine),
            bottom: const BorderSide(color: kLine)),
      ),
      padding: const EdgeInsets.all(13),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(_emoji(sev), style: const TextStyle(fontSize: 15)),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${it['title']}',
                style: const TextStyle(fontWeight: FontWeight.w600, color: kInk)),
            const SizedBox(height: 2),
            Text('${it['detail']}',
                style: const TextStyle(fontSize: 12.5, color: kMuted)),
          ]),
        ),
        const SizedBox(width: 8),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
          decoration: BoxDecoration(
            color: kBg, borderRadius: BorderRadius.circular(20)),
          child: Text('${it['category']}',
              style: const TextStyle(fontSize: 10.5, color: kMuted)),
        ),
      ]),
    );
  }
}

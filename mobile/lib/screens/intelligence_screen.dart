import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import 'attention_screen.dart';
import 'lulaai_screen.dart';
import 'predictions_screen.dart';

/// Lulaworks Intelligence — the mobile AI command centre. One hub tying LulaAI,
/// the Attention Centre and Predictions together, with live counts. Parity with
/// the web /intelligence/ page.
class IntelligenceScreen extends StatefulWidget {
  const IntelligenceScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<IntelligenceScreen> createState() => _IntelligenceScreenState();
}

class _IntelligenceScreenState extends State<IntelligenceScreen> {
  late Future<Map<String, int>> _future = _load();

  Future<Map<String, int>> _load() async {
    final r = await Future.wait([_attention(), _predictions()]);
    return {'attention': r[0], 'predictions': r[1]};
  }

  Future<int> _attention() async {
    try {
      final body = await widget.api.get('/attention/');
      if (body is Map && body['total'] is int) return body['total'] as int;
    } catch (_) {}
    return 0;
  }

  Future<int> _predictions() async {
    try {
      final body = await widget.api.get('/ai/predictions/');
      final list = (body is Map ? body['predictions'] : null) as List?;
      return list?.length ?? 0;
    } catch (_) {}
    return 0;
  }

  Future<void> _refresh() async {
    setState(() => _future = _load());
    await _future;
  }

  void _push(Widget s) async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => s));
    _refresh();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Intelligence'), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: _refresh,
        child: FutureBuilder<Map<String, int>>(
          future: _future,
          builder: (context, snap) {
            final c = snap.data ?? const {'attention': 0, 'predictions': 0};
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Padding(
                  padding: EdgeInsets.fromLTRB(2, 2, 2, 12),
                  child: Text('What your business needs now, what’s coming, and what '
                      'Lulaworks is watching for you.',
                      style: TextStyle(fontSize: 13, color: kMuted, height: 1.4)),
                ),
                _tile(Icons.notifications_active_outlined, 'Attention',
                    'Exceptions that need a human now', c['attention'],
                    () => _push(AttentionScreen(api: widget.api))),
                _tile(Icons.insights_outlined, 'Predictions',
                    'What’s likely coming — with confidence', c['predictions'],
                    () => _push(PredictionsScreen(api: widget.api))),
                _tile(Icons.auto_awesome_outlined, 'Ask LulaAI',
                    'Your grounded AI assistant', null,
                    () => _push(LulaAiScreen(api: widget.api))),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _tile(IconData icon, String title, String subtitle, int? count, VoidCallback onTap) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: kLine),
      ),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        leading: Container(
          width: 44, height: 44,
          decoration: BoxDecoration(
              color: kBrand.withOpacity(0.08), borderRadius: BorderRadius.circular(12)),
          child: Icon(icon, color: kBrandDark, size: 22),
        ),
        title: Text(title,
            style: const TextStyle(fontSize: 15.5, fontWeight: FontWeight.w600, color: kInk)),
        subtitle: Text(subtitle, style: const TextStyle(fontSize: 12.5, color: kMuted)),
        trailing: Row(mainAxisSize: MainAxisSize.min, children: [
          if (count != null && count > 0)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
              decoration: BoxDecoration(color: kBrandTint, borderRadius: BorderRadius.circular(20)),
              child: Text('$count',
                  style: const TextStyle(fontSize: 12.5, color: kBrandDark, fontWeight: FontWeight.w700)),
            ),
          const SizedBox(width: 6),
          const Icon(Icons.chevron_right, color: kMuted),
        ]),
        onTap: onTap,
      ),
    );
  }
}

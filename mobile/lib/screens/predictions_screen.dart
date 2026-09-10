import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';

/// AI Predictions — grounded foresight (rising prices, repeat customers, jobs at
/// risk) with confidence, reasoning and the source record. Read-only mirror of
/// the web command centre; permission-scoped server-side.
class PredictionsScreen extends StatefulWidget {
  const PredictionsScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<PredictionsScreen> createState() => _PredictionsScreenState();
}

class _PredictionsScreenState extends State<PredictionsScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async {
    final body = await widget.api.get('/ai/predictions/');
    final list = (body is Map ? body['predictions'] : null) as List? ?? const [];
    return list.map((e) => (e as Map).cast<String, dynamic>()).toList();
  }

  Future<void> _refresh() async {
    setState(() => _future = _load());
    await _future;
  }

  IconData _icon(String kind) => switch (kind) {
        'price_rise' => Icons.trending_up,
        'repeat_customer' => Icons.autorenew,
        'job_delay' => Icons.schedule,
        _ => Icons.insights_outlined,
      };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Predictions'), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: _refresh,
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator(color: kBrand));
            }
            if (snap.hasError) {
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('Could not load predictions.', style: TextStyle(color: kMuted))),
              ]);
            }
            final preds = snap.data!;
            if (preds.isEmpty) {
              return ListView(children: const [
                SizedBox(height: 100),
                Padding(
                  padding: EdgeInsets.symmetric(horizontal: 32),
                  child: Center(child: Text(
                      'No predictions yet. They appear as Lulaworks builds up price '
                      'history, quoting patterns and job data.',
                      textAlign: TextAlign.center, style: TextStyle(color: kMuted))),
                ),
              ]);
            }
            return ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: preds.length,
              separatorBuilder: (_, __) => const SizedBox(height: 12),
              itemBuilder: (_, i) => _card(preds[i]),
            );
          },
        ),
      ),
    );
  }

  Widget _card(Map<String, dynamic> p) {
    final reasoning = (p['reasoning'] as List?)?.cast<String>() ?? const [];
    final conf = ((p['confidence'] ?? 0) as num).toDouble();
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: kLine),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Container(
            width: 34, height: 34, alignment: Alignment.center,
            decoration: BoxDecoration(
                color: kBrand.withOpacity(0.10), borderRadius: BorderRadius.circular(10)),
            child: Icon(_icon('${p['kind']}'), size: 18, color: kBrandDark),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text('${p['subject'] ?? ''}',
                style: const TextStyle(fontSize: 14.5, fontWeight: FontWeight.w700, color: kInk)),
          ),
          _confChip(conf),
        ]),
        const SizedBox(height: 10),
        Text('${p['statement'] ?? ''}',
            style: const TextStyle(fontSize: 13.5, color: kInk, height: 1.35)),
        if (reasoning.isNotEmpty) const SizedBox(height: 8),
        for (final r in reasoning)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text('• $r', style: const TextStyle(fontSize: 12, color: kMuted, height: 1.3)),
          ),
        if ('${p['source'] ?? ''}'.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text('Source: ${p['source']}',
              style: const TextStyle(fontSize: 11.5, color: kMuted, fontStyle: FontStyle.italic)),
        ],
      ]),
    );
  }

  Widget _confChip(double conf) {
    final pct = (conf * 100).round();
    final strong = conf >= 0.75;
    final c = strong ? kBrandDark : kOrange;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: c.withOpacity(0.12), borderRadius: BorderRadius.circular(20)),
      child: Text('$pct%',
          style: TextStyle(fontSize: 11.5, color: c, fontWeight: FontWeight.w700)),
    );
  }
}

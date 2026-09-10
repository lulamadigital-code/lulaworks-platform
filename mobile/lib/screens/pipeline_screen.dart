import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';

/// Sales pipeline — open deals grouped by stage, with a tap to move a deal
/// forward (or mark it Won/Lost). Parity with the web CRM pipeline. Deals are
/// created by converting a lead or from a customer.
class PipelineScreen extends StatefulWidget {
  const PipelineScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<PipelineScreen> createState() => _PipelineScreenState();
}

class _PipelineScreenState extends State<PipelineScreen> {
  late Future<_Pipeline> _future = _load();

  Future<_Pipeline> _load() async {
    final r = await Future.wait([
      widget.api.get('/opportunities/stages/'),
      widget.api.get('/opportunities/?stage=open'),
    ]);
    final stages = (r[0] as List).cast<Map<String, dynamic>>();
    final opps = pageResults(r[1]).cast<Map<String, dynamic>>();
    return _Pipeline(stages, opps);
  }

  void _reload() => setState(() => _future = _load());
  bool get _canEdit => widget.api.can('crm.manage');

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Pipeline')),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => _reload(),
        child: FutureBuilder<_Pipeline>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator(color: kBrand));
            }
            if (snap.hasError || !snap.hasData) {
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('Could not load the pipeline.',
                    style: TextStyle(color: kMuted))),
              ]);
            }
            final p = snap.data!;
            // open stages only (won/lost aren't columns)
            final openStages = p.stages
                .where((s) => s['value'] != 'won' && s['value'] != 'lost')
                .toList();
            if (p.opps.isEmpty) {
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('No open deals.\nConvert a lead to start one.',
                    textAlign: TextAlign.center, style: TextStyle(color: kMuted))),
              ]);
            }
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                for (final st in openStages)
                  ..._stageSection(st, p.opps.where((o) => o['stage'] == st['value']).toList()),
              ],
            );
          },
        ),
      ),
    );
  }

  List<Widget> _stageSection(Map<String, dynamic> stage, List<Map<String, dynamic>> deals) {
    if (deals.isEmpty) return const [];
    final total = deals.fold<double>(
        0, (s, d) => s + (double.tryParse('${d['estimated_value'] ?? 0}') ?? 0));
    return [
      Padding(
        padding: const EdgeInsets.only(top: 8, bottom: 8),
        child: Row(children: [
          Text('${stage['label']}'.toUpperCase(),
              style: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700,
                  letterSpacing: .5, color: kMuted)),
          const SizedBox(width: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
            decoration: BoxDecoration(color: kBrandTint, borderRadius: BorderRadius.circular(20)),
            child: Text('${deals.length}',
                style: const TextStyle(fontSize: 11, color: kBrandDark, fontWeight: FontWeight.w700)),
          ),
          const Spacer(),
          Text(widget.api.money(total), style: const TextStyle(fontSize: 12, color: kMuted)),
        ]),
      ),
      for (final d in deals) _dealCard(d),
      const SizedBox(height: 8),
    ];
  }

  Widget _dealCard(Map<String, dynamic> d) {
    final value = double.tryParse('${d['estimated_value'] ?? 0}') ?? 0;
    return InkWell(
      borderRadius: BorderRadius.circular(12),
      onTap: _canEdit ? () => _moveSheet(d) : null,
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.all(13),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: kLine),
        ),
        child: Row(children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${d['title']}',
                  style: const TextStyle(fontWeight: FontWeight.w600, color: kInk)),
              Text('${d['customer_name'] ?? ''}',
                  style: const TextStyle(fontSize: 12.5, color: kMuted)),
              if (value > 0)
                Text(widget.api.money(value),
                    style: const TextStyle(fontSize: 12.5, color: kMuted)),
            ]),
          ),
          if (_canEdit) const Icon(Icons.swap_vert, size: 18, color: kMuted),
        ]),
      ),
    );
  }

  Future<void> _moveSheet(Map<String, dynamic> deal) async {
    final messenger = ScaffoldMessenger.of(context);
    final p = await _future;
    if (!mounted) return;
    final chosen = await showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Padding(
            padding: EdgeInsets.all(14),
            child: Text('Move deal to…',
                style: TextStyle(fontWeight: FontWeight.w700, color: kInk)),
          ),
          for (final s in p.stages)
            ListTile(
              title: Text('${s['label']}'),
              trailing: s['value'] == deal['stage']
                  ? const Icon(Icons.check, color: kBrand)
                  : null,
              onTap: () => Navigator.pop(ctx, '${s['value']}'),
            ),
        ]),
      ),
    );
    if (chosen == null || chosen == deal['stage'] || !mounted) return;
    String reason = '';
    if (chosen == 'lost') {
      reason = await _askReason() ?? '';
    }
    try {
      await widget.api.post('/opportunities/${deal['id']}/stage/',
          {'stage': chosen, if (reason.isNotEmpty) 'reason': reason});
      _reload();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  Future<String?> _askReason() {
    final c = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Why lost?'),
        content: TextField(controller: c,
            decoration: const InputDecoration(labelText: 'Reason (optional)')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, ''), child: const Text('Skip')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: kBrand),
            onPressed: () => Navigator.pop(ctx, c.text.trim()),
            child: const Text('Mark lost')),
        ],
      ),
    );
  }
}

class _Pipeline {
  _Pipeline(this.stages, this.opps);
  final List<Map<String, dynamic>> stages;
  final List<Map<String, dynamic>> opps;
}

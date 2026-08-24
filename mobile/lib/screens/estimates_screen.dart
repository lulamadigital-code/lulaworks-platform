import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../widgets/status_pill.dart';

/// Estimates — the pre-quote costing. List + detail. Pricing fields are money,
/// so they only appear for users the backend grants them to (api.money renders a
/// withheld value as '—'). Approve is available with estimating.approve.
class EstimatesScreen extends StatefulWidget {
  const EstimatesScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<EstimatesScreen> createState() => _EstimatesScreenState();
}

class _EstimatesScreenState extends State<EstimatesScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('/estimates/'));

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Estimates')),
      body: RefreshIndicator(
        onRefresh: () async => setState(() { _future = _load(); }),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 100),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
              ]);
            }
            final rows = snap.data ?? const [];
            if (rows.isEmpty) {
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('No estimates yet.')),
              ]);
            }
            return ListView.separated(
              itemCount: rows.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final e = rows[i];
                final title = '${e['title'] ?? ''}'.isNotEmpty
                    ? '${e['title']}'
                    : '${e['work_type'] ?? e['number']}';
                return ListTile(
                  title: Text('${e['number']}  ·  $title',
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  subtitle: Text('${e['client_name'] ?? ''}'),
                  trailing: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      StatusPill(status: '${e['status']}'),
                      const SizedBox(height: 4),
                      Text(widget.api.money(e['selling_price']),
                          style: const TextStyle(
                              fontSize: 12.5, fontWeight: FontWeight.w600)),
                    ],
                  ),
                  isThreeLine: true,
                  onTap: () async {
                    final changed = await Navigator.of(context).push<bool>(
                        MaterialPageRoute(
                            builder: (_) => _EstimateDetail(
                                api: widget.api, estId: '${e['id']}')));
                    if (changed == true) setState(() { _future = _load(); });
                  },
                );
              },
            );
          },
        ),
      ),
    );
  }
}

class _EstimateDetail extends StatefulWidget {
  const _EstimateDetail({required this.api, required this.estId});
  final ApiClient api;
  final String estId;

  @override
  State<_EstimateDetail> createState() => _EstimateDetailState();
}

class _EstimateDetailState extends State<_EstimateDetail> {
  late Future<Map<String, dynamic>> _future = _load();
  bool _changed = false;

  Future<Map<String, dynamic>> _load() async =>
      (await widget.api.get('/estimates/${widget.estId}/') as Map)
          .cast<String, dynamic>();

  Future<void> _approve() async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('/estimates/${widget.estId}/approve/');
      _changed = true;
      setState(() { _future = _load(); });
      messenger.showSnackBar(const SnackBar(content: Text('Estimate approved')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission to approve estimates."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<Map<String, dynamic>>(
      future: _future,
      builder: (context, snap) {
        final e = snap.data;
        return Scaffold(
          appBar: AppBar(
            title: Text('${e?['number'] ?? 'Estimate'}'),
            leading: BackButton(onPressed: () => Navigator.pop(context, _changed)),
          ),
          body: e == null
              ? const Center(child: CircularProgressIndicator())
              : _body(context, e),
        );
      },
    );
  }

  Widget _body(BuildContext context, Map<String, dynamic> e) {
    final flags = (e['risk_flags'] as List?) ?? const [];
    final money = <(String, dynamic)>[
      ('Direct cost', e['direct_cost']),
      ('Contingency', e['contingency_amount']),
      ('Total cost', e['total_cost']),
      ('Selling price', e['selling_price']),
      ('Margin', e['margin_amount']),
    ];
    final hasMoney = money.any((m) => m.$2 != null);
    return ListView(padding: const EdgeInsets.all(16), children: [
      Row(children: [
        Expanded(child: Text('${e['client_name'] ?? ''}',
            style: Theme.of(context).textTheme.titleMedium)),
        StatusPill(status: '${e['status']}'),
      ]),
      if ('${e['work_type'] ?? ''}'.isNotEmpty)
        Text('${e['work_type']} · v${e['version'] ?? 1}',
            style: Theme.of(context).textTheme.bodySmall),
      const SizedBox(height: 16),
      if (hasMoney) ...[
        Card(
          child: Column(children: [
            for (final m in money)
              if (m.$2 != null)
                ListTile(
                  dense: true,
                  title: Text(m.$1),
                  trailing: Text(widget.api.money(m.$2),
                      style: TextStyle(
                          fontWeight: m.$1 == 'Selling price'
                              ? FontWeight.bold
                              : FontWeight.w500)),
                ),
            if (e['margin_pct'] != null)
              ListTile(
                dense: true,
                title: const Text('Margin %'),
                trailing: Text('${e['margin_pct']}%'),
              ),
          ]),
        ),
        const SizedBox(height: 16),
      ] else
        Padding(
          padding: const EdgeInsets.only(bottom: 16),
          child: Text('Pricing is hidden for your role.',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.outline)),
        ),
      if (flags.isNotEmpty) ...[
        Text('Risk flags', style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: 6),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final f in flags)
            Chip(
              avatar: const Icon(Icons.flag_outlined, size: 16),
              label: Text('$f'),
              visualDensity: VisualDensity.compact,
            ),
        ]),
        const SizedBox(height: 16),
      ],
      if ('${e['status']}' != 'approved' && widget.api.canApproveEstimate)
        FilledButton.icon(
          onPressed: _approve,
          icon: const Icon(Icons.verified),
          label: const Text('Approve estimate'),
        ),
    ]);
  }
}

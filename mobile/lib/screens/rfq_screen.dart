import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../widgets/status_pill.dart';

/// RFQs — uploaded request documents parsed by the backend. View-only on mobile:
/// the list and the extracted fields/lines. Creating/uploading stays on the web.
class RfqScreen extends StatefulWidget {
  const RfqScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<RfqScreen> createState() => _RfqScreenState();
}

class _RfqScreenState extends State<RfqScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('/rfqs/'));

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('RFQs')),
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
                Center(child: Text('No RFQs yet.')),
              ]);
            }
            return ListView.separated(
              itemCount: rows.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final r = rows[i];
                return ListTile(
                  leading: const Icon(Icons.description_outlined),
                  title: Text('${r['original_name'] ?? r['doc_class'] ?? 'RFQ'}',
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  subtitle: Text('${r['doc_class'] ?? ''}'
                      '${r['quotation_number'] != null ? ' · ${r['quotation_number']}' : ''}'),
                  trailing: StatusPill(status: '${r['status']}'),
                  onTap: () => Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => _RfqDetail(rfq: r))),
                );
              },
            );
          },
        ),
      ),
    );
  }
}

class _RfqDetail extends StatelessWidget {
  const _RfqDetail({required this.rfq});
  final Map<String, dynamic> rfq;

  @override
  Widget build(BuildContext context) {
    final fields = (rfq['fields'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    final lines = (rfq['lines'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    final warnings = (rfq['warnings'] as List?) ?? const [];
    return Scaffold(
      appBar: AppBar(title: Text('${rfq['original_name'] ?? 'RFQ'}')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Row(children: [
          StatusPill(status: '${rfq['status']}'),
          const Spacer(),
          if ('${rfq['quotation_number'] ?? ''}'.isNotEmpty)
            Text('Quote ${rfq['quotation_number']}',
                style: Theme.of(context).textTheme.bodySmall),
        ]),
        if (warnings.isNotEmpty) ...[
          const SizedBox(height: 12),
          for (final w in warnings)
            Row(children: [
              const Icon(Icons.warning_amber, size: 16, color: Colors.orange),
              const SizedBox(width: 6),
              Expanded(child: Text('$w', style: Theme.of(context).textTheme.bodySmall)),
            ]),
        ],
        const SizedBox(height: 16),
        if (fields.isNotEmpty) ...[
          Text('Extracted fields', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 6),
          Card(
            child: Column(children: [
              for (final f in fields)
                ListTile(
                  dense: true,
                  title: Text('${f['key'] ?? ''}'),
                  trailing: Flexible(
                      child: Text('${f['value'] ?? ''}',
                          textAlign: TextAlign.right, overflow: TextOverflow.ellipsis)),
                ),
            ]),
          ),
          const SizedBox(height: 16),
        ],
        if (lines.isNotEmpty) ...[
          Text('Lines (${lines.length})', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 6),
          for (final l in lines)
            Card(
              margin: const EdgeInsets.only(bottom: 8),
              child: ListTile(
                dense: true,
                title: Text('${l['description'] ?? l['item'] ?? l['name'] ?? '—'}'),
                subtitle: Text('Qty ${l['quantity'] ?? l['qty'] ?? '—'}'
                    '${l['unit'] != null ? ' ${l['unit']}' : ''}'),
              ),
            ),
        ],
      ]),
    );
  }
}

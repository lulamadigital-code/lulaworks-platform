import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';

/// Suppliers — searchable list and a read detail. Sourcing is a field activity,
/// so this is view-first; editing lives on the web for now.
class SuppliersScreen extends StatefulWidget {
  const SuppliersScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<SuppliersScreen> createState() => _SuppliersScreenState();
}

class _SuppliersScreenState extends State<SuppliersScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('/suppliers/'));

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Suppliers')),
      body: RefreshIndicator(
        onRefresh: () async => setState(() { _future = _load(); }),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return _err(context, snap.error);
            }
            final rows = snap.data ?? const [];
            if (rows.isEmpty) return _empty('No suppliers yet.');
            return ListView.separated(
              itemCount: rows.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final s = rows[i];
                final cats = (s['categories'] as List?)?.join(', ') ?? '';
                return ListTile(
                  leading: CircleAvatar(
                    backgroundColor:
                        Theme.of(context).colorScheme.primary.withOpacity(0.12),
                    child: Icon(Icons.local_shipping_outlined,
                        color: Theme.of(context).colorScheme.primary, size: 20),
                  ),
                  title: Row(children: [
                    Flexible(child: Text('${s['name']}', overflow: TextOverflow.ellipsis)),
                    if (s['preferred'] == true) ...[
                      const SizedBox(width: 6),
                      const Icon(Icons.star, size: 15, color: Colors.amber),
                    ],
                  ]),
                  subtitle: Text(cats.isEmpty ? '${s['contact_person'] ?? ''}' : cats,
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  trailing: s['performance_score'] != null
                      ? Text('${s['performance_score']}',
                          style: const TextStyle(fontWeight: FontWeight.w600))
                      : null,
                  onTap: () => Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => _SupplierDetail(supplier: s))),
                );
              },
            );
          },
        ),
      ),
    );
  }
}

class _SupplierDetail extends StatelessWidget {
  const _SupplierDetail({required this.supplier});
  final Map<String, dynamic> supplier;

  @override
  Widget build(BuildContext context) {
    final s = supplier;
    final cats = (s['categories'] as List?)?.cast<String>() ?? const [];
    final rows = <(IconData, String)>[
      if ('${s['contact_person'] ?? ''}'.isNotEmpty) (Icons.person_outline, '${s['contact_person']}'),
      if ('${s['email'] ?? ''}'.isNotEmpty) (Icons.email_outlined, '${s['email']}'),
      if ('${s['phone'] ?? ''}'.isNotEmpty) (Icons.phone_outlined, '${s['phone']}'),
      if ('${s['payment_terms'] ?? ''}'.isNotEmpty) (Icons.payments_outlined, 'Terms: ${s['payment_terms']}'),
      if ('${s['vat_no'] ?? ''}'.isNotEmpty) (Icons.receipt_long_outlined, 'VAT ${s['vat_no']}'),
      if (s['bee_level'] != null) (Icons.verified_outlined, 'B-BBEE level ${s['bee_level']}'),
    ];
    return Scaffold(
      appBar: AppBar(title: Text('${s['name']}')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        if (cats.isNotEmpty)
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final ct in cats)
              Chip(label: Text(ct), visualDensity: VisualDensity.compact),
          ]),
        if (cats.isNotEmpty) const SizedBox(height: 14),
        Card(
          child: Column(children: [
            for (final r in rows)
              ListTile(dense: true, leading: Icon(r.$1, size: 20), title: Text(r.$2)),
          ]),
        ),
        if ('${s['notes'] ?? ''}'.isNotEmpty) ...[
          const SizedBox(height: 16),
          Text('Notes', style: Theme.of(context).textTheme.labelLarge),
          const SizedBox(height: 4),
          Text('${s['notes']}'),
        ],
      ]),
    );
  }
}

Widget _err(BuildContext context, Object? e) => ListView(children: [
      const SizedBox(height: 100),
      Center(child: Text('$e', textAlign: TextAlign.center)),
    ]);
Widget _empty(String msg) =>
    ListView(children: [const SizedBox(height: 120), Center(child: Text(msg))]);

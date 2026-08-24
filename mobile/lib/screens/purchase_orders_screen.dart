import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../widgets/status_pill.dart';

/// Purchase orders (us → supplier). List + detail, with approve/receive actions
/// gated by permission (po.approve to approve, procurement.manage to receive).
/// `total` is money — the backend withholds it under the Golden Rule, and
/// api.money() renders the withheld case as '—'.
class PurchaseOrdersScreen extends StatefulWidget {
  const PurchaseOrdersScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<PurchaseOrdersScreen> createState() => _PurchaseOrdersScreenState();
}

class _PurchaseOrdersScreenState extends State<PurchaseOrdersScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('/purchase-orders/'));

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Purchase orders')),
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
                Center(child: Text('No purchase orders yet.')),
              ]);
            }
            return ListView.separated(
              itemCount: rows.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final po = rows[i];
                return ListTile(
                  title: Text('${po['number']}'),
                  subtitle: Text('${po['supplier_name'] ?? ''}'),
                  trailing: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      StatusPill(status: '${po['status']}'),
                      const SizedBox(height: 4),
                      Text(widget.api.money(po['total']),
                          style: const TextStyle(
                              fontSize: 12.5, fontWeight: FontWeight.w600)),
                    ],
                  ),
                  isThreeLine: true,
                  onTap: () async {
                    final changed = await Navigator.of(context).push<bool>(
                        MaterialPageRoute(
                            builder: (_) =>
                                _PODetail(api: widget.api, poId: '${po['id']}')));
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

class _PODetail extends StatefulWidget {
  const _PODetail({required this.api, required this.poId});
  final ApiClient api;
  final String poId;

  @override
  State<_PODetail> createState() => _PODetailState();
}

class _PODetailState extends State<_PODetail> {
  late Future<Map<String, dynamic>> _future = _load();
  bool _changed = false;

  Future<Map<String, dynamic>> _load() async =>
      (await widget.api.get('/purchase-orders/${widget.poId}/') as Map)
          .cast<String, dynamic>();

  Future<void> _action(String path, String label) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('/purchase-orders/${widget.poId}/$path/');
      _changed = true;
      setState(() { _future = _load(); });
      messenger.showSnackBar(SnackBar(content: Text('$label — done')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission to $label.".toLowerCase()
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: true,
      onPopInvoked: (_) {},
      child: FutureBuilder<Map<String, dynamic>>(
        future: _future,
        builder: (context, snap) {
          final po = snap.data;
          return Scaffold(
            appBar: AppBar(
              title: Text('${po?['number'] ?? 'Purchase order'}'),
              leading: BackButton(onPressed: () => Navigator.pop(context, _changed)),
            ),
            body: po == null
                ? const Center(child: CircularProgressIndicator())
                : _body(context, po),
          );
        },
      ),
    );
  }

  Widget _body(BuildContext context, Map<String, dynamic> po) {
    final lines = (po['lines'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    final status = '${po['status']}';
    return ListView(padding: const EdgeInsets.all(16), children: [
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Expanded(child: Text('${po['supplier_name'] ?? ''}',
            style: Theme.of(context).textTheme.titleMedium)),
        StatusPill(status: status),
      ]),
      const SizedBox(height: 8),
      if ('${po['delivery_address'] ?? ''}'.isNotEmpty)
        Text('Deliver to: ${po['delivery_address']}',
            style: Theme.of(context).textTheme.bodyMedium),
      if ('${po['payment_terms'] ?? ''}'.isNotEmpty)
        Text('Terms: ${po['payment_terms']}',
            style: Theme.of(context).textTheme.bodyMedium),
      const SizedBox(height: 16),
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
            trailing: Text(widget.api.money(l['amount'] ?? l['line_total'] ?? l['total']),
                style: const TextStyle(fontWeight: FontWeight.w600)),
          ),
        ),
      const Divider(height: 24),
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text('Total', style: Theme.of(context).textTheme.titleMedium),
        Text(widget.api.money(po['total']),
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.bold)),
      ]),
      const SizedBox(height: 20),
      Row(children: [
        if (status == 'draft' && widget.api.canApprovePO)
          Expanded(
            child: FilledButton.icon(
              onPressed: () => _action('approve', 'Approve'),
              icon: const Icon(Icons.verified),
              label: const Text('Approve'),
            ),
          ),
        if (status == 'approved' && widget.api.canProcurement) ...[
          if (status == 'draft') const SizedBox(width: 12),
          Expanded(
            child: FilledButton.tonalIcon(
              onPressed: () => _action('receive', 'Receive'),
              icon: const Icon(Icons.inventory_2_outlined),
              label: const Text('Receive'),
            ),
          ),
        ],
      ]),
    ]);
  }
}

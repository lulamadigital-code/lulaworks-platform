import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/status_pill.dart';

/// Tax invoices & delivery notes. Two tabs over one endpoint (?kind=). Invoices
/// show money (Golden-Rule gated); delivery notes show quantities only — the
/// backend never sends prices for them (§15), so there's nothing to leak here.
class CommercialDocumentsScreen extends StatelessWidget {
  const CommercialDocumentsScreen({super.key, required this.api});
  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Invoices & delivery'),
          bottom: const TabBar(tabs: [
            Tab(text: 'Tax invoices'),
            Tab(text: 'Delivery notes'),
          ]),
        ),
        body: TabBarView(children: [
          _DocList(api: api, kind: 'invoice'),
          _DocList(api: api, kind: 'delivery'),
        ]),
      ),
    );
  }
}

class _DocList extends StatefulWidget {
  const _DocList({required this.api, required this.kind});
  final ApiClient api;
  final String kind;

  @override
  State<_DocList> createState() => _DocListState();
}

class _DocListState extends State<_DocList>
    with AutomaticKeepAliveClientMixin {
  late Future<List<Map<String, dynamic>>> _future = _load();

  @override
  bool get wantKeepAlive => true;

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('/commercial-documents/?kind=${widget.kind}'));

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return RefreshIndicator(
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
            return ListView(children: [
              const SizedBox(height: 120),
              Center(child: Text('No ${widget.kind == 'invoice' ? 'invoices' : 'delivery notes'} yet.')),
            ]);
          }
          return ListView.separated(
            itemCount: rows.length,
            separatorBuilder: (_, __) => const Divider(height: 1),
            itemBuilder: (context, i) {
              final d = rows[i];
              final isInvoice = d['kind'] == 'invoice';
              return ListTile(
                title: Text('${d['number']}  ·  ${d['client_name'] ?? ''}',
                    maxLines: 1, overflow: TextOverflow.ellipsis),
                subtitle: isInvoice
                    ? _invoiceSubtitle(context, d)
                    : Text('${d['quotation_number'] ?? ''}'),
                trailing: StatusPill(status: '${d['status']}'),
                onTap: () async {
                  final changed = await Navigator.of(context).push<bool>(
                      MaterialPageRoute(
                          builder: (_) => _DocDetail(
                              api: widget.api, docId: '${d['id']}')));
                  if (changed == true) setState(() { _future = _load(); });
                },
              );
            },
          );
        },
      ),
    );
  }

  Widget _invoiceSubtitle(BuildContext context, Map<String, dynamic> d) {
    final state = '${d['payment_state'] ?? ''}';
    final total = widget.api.money(d['total']);
    if (state.isEmpty) return Text(total);
    final color = state == 'paid'
        ? kGreen
        : state == 'part'
            ? kOrange
            : kRed;
    return Row(children: [
      Text('$total · '),
      Text(state == 'paid' ? 'Paid' : state == 'part' ? 'Part-paid' : 'Unpaid',
          style: TextStyle(color: color, fontWeight: FontWeight.w600)),
    ]);
  }
}

class _DocDetail extends StatefulWidget {
  const _DocDetail({required this.api, required this.docId});
  final ApiClient api;
  final String docId;

  @override
  State<_DocDetail> createState() => _DocDetailState();
}

class _DocDetailState extends State<_DocDetail> {
  late Future<_Doc> _future = _load();
  bool _changed = false;

  Future<_Doc> _load() async {
    final id = widget.docId;
    final results = await Future.wait([
      widget.api.get('/commercial-documents/$id/'),
      widget.api.get('/commercial-documents/$id/workflow/').catchError((_) => null),
    ]);
    return _Doc(
      doc: (results[0] as Map).cast<String, dynamic>(),
      workflow: results[1] is Map ? (results[1] as Map).cast<String, dynamic>() : const {},
    );
  }

  Future<void> _transition(String to, String label) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('/commercial-documents/${widget.docId}/transition/',
          {'to_status': to});
      _changed = true;
      setState(() { _future = _load(); });
      messenger.showSnackBar(SnackBar(content: Text('Moved to $label')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  Future<void> _recordPayment() async {
    final amount = TextEditingController();
    final ref = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Record payment'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(
            controller: amount,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(labelText: 'Amount'),
          ),
          TextField(
            controller: ref,
            decoration: const InputDecoration(labelText: 'Reference (optional)'),
          ),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Save')),
        ],
      ),
    );
    if (ok != true || amount.text.trim().isEmpty) return;
    if (!mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('/commercial-documents/${widget.docId}/payment/',
          {'amount': amount.text.trim(), 'reference': ref.text.trim()});
      _changed = true;
      setState(() { _future = _load(); });
      messenger.showSnackBar(const SnackBar(content: Text('Payment recorded')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<_Doc>(
      future: _future,
      builder: (context, snap) {
        final doc = snap.data?.doc;
        return Scaffold(
          appBar: AppBar(
            title: Text('${doc?['number'] ?? 'Document'}'),
            leading: BackButton(onPressed: () => Navigator.pop(context, _changed)),
          ),
          body: doc == null
              ? const Center(child: CircularProgressIndicator())
              : (doc['kind'] == 'invoice'
                  ? _invoiceBody(context, snap.data!)
                  : _deliveryBody(context, snap.data!)),
        );
      },
    );
  }

  Widget _invoiceBody(BuildContext context, _Doc d) {
    final doc = d.doc;
    final lines = (doc['lines'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    final outstanding = double.tryParse('${doc['outstanding']}') ?? 0;
    final payments = (doc['payments'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    return ListView(padding: const EdgeInsets.all(16), children: [
      _headerRow(context, doc),
      const SizedBox(height: 16),
      Text('Line items', style: Theme.of(context).textTheme.titleSmall),
      const SizedBox(height: 6),
      for (final l in lines)
        Card(
          margin: const EdgeInsets.only(bottom: 8),
          child: ListTile(
            dense: true,
            title: Text('${l['description'] ?? '—'}'),
            subtitle: Text('${l['qty']} ${l['unit'] ?? ''}'
                ' × ${widget.api.money(l['unit_price'])}'),
            trailing: Text(widget.api.money(l['line_total']),
                style: const TextStyle(fontWeight: FontWeight.w600)),
          ),
        ),
      const Divider(height: 24),
      _amount(context, 'Invoice total', widget.api.money(doc['total']), bold: true),
      _amount(context, 'Paid', widget.api.money(doc['amount_paid'])),
      _amount(context, 'Outstanding', widget.api.money(doc['outstanding']),
          color: outstanding > 0 ? kRed : kGreen),
      if (payments.isNotEmpty) ...[
        const SizedBox(height: 16),
        Text('Payments', style: Theme.of(context).textTheme.titleSmall),
        for (final p in payments)
          ListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.payments_outlined, size: 20),
            title: Text(widget.api.money(p['amount'])),
            subtitle: Text('${p['date'] ?? ''}'
                '${'${p['reference'] ?? ''}'.isNotEmpty ? ' · ${p['reference']}' : ''}'),
          ),
      ],
      const SizedBox(height: 20),
      Row(children: [
        if (outstanding > 0 && widget.api.canRecordPayment)
          Expanded(
            child: FilledButton.tonalIcon(
              onPressed: _recordPayment,
              icon: const Icon(Icons.add_card),
              label: const Text('Record payment'),
            ),
          ),
      ]),
      _workflow(context, d),
    ]);
  }

  Widget _deliveryBody(BuildContext context, _Doc d) {
    final doc = d.doc;
    final lines = (doc['lines'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    return ListView(padding: const EdgeInsets.all(16), children: [
      _headerRow(context, doc),
      const SizedBox(height: 8),
      if ('${doc['delivery_address'] ?? ''}'.isNotEmpty)
        Text('Deliver to: ${doc['delivery_address']}',
            style: Theme.of(context).textTheme.bodyMedium),
      if ('${doc['delivery_date'] ?? ''}'.isNotEmpty)
        Text('Date: ${doc['delivery_date']}',
            style: Theme.of(context).textTheme.bodyMedium),
      const SizedBox(height: 16),
      Text('Items delivered (${lines.length})',
          style: Theme.of(context).textTheme.titleSmall),
      const SizedBox(height: 6),
      // Quantities only — a delivery note never shows prices (§15).
      for (final l in lines)
        Card(
          margin: const EdgeInsets.only(bottom: 8),
          child: ListTile(
            dense: true,
            title: Text('${l['description'] ?? '—'}'),
            trailing: Text('${l['qty']} ${l['unit'] ?? ''}',
                style: const TextStyle(fontWeight: FontWeight.w600)),
          ),
        ),
      if ('${doc['delivery_notes'] ?? ''}'.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text('Notes', style: Theme.of(context).textTheme.labelLarge),
        Text('${doc['delivery_notes']}'),
      ],
      _workflow(context, d),
    ]);
  }

  Widget _headerRow(BuildContext context, Map<String, dynamic> doc) {
    return Row(children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${doc['client_name'] ?? ''}',
              style: Theme.of(context).textTheme.titleMedium),
          if ('${doc['quotation_number'] ?? ''}'.isNotEmpty)
            Text('From ${doc['quotation_number']}',
                style: Theme.of(context).textTheme.bodySmall),
        ]),
      ),
      StatusPill(status: '${doc['status']}'),
    ]);
  }

  Widget _workflow(BuildContext context, _Doc d) {
    final next = (d.workflow['next'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    if (next.isEmpty || !widget.api.canTransitionCommercial) {
      return const SizedBox(height: 24);
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const SizedBox(height: 20),
      Text('Move to', style: Theme.of(context).textTheme.titleSmall),
      const SizedBox(height: 8),
      Wrap(spacing: 8, runSpacing: 8, children: [
        for (final n in next)
          FilledButton.tonal(
            onPressed: () => _transition('${n['value']}', '${n['label']}'),
            child: Text('${n['label']}'),
          ),
      ]),
      const SizedBox(height: 24),
    ]);
  }

  Widget _amount(BuildContext context, String label, String value,
      {bool bold = false, Color? color}) {
    final style = (bold
            ? Theme.of(context).textTheme.titleMedium
            : Theme.of(context).textTheme.bodyMedium)
        ?.copyWith(color: color, fontWeight: bold ? FontWeight.bold : null);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text(label, style: style),
        Text(value, style: style),
      ]),
    );
  }
}

class _Doc {
  _Doc({required this.doc, required this.workflow});
  final Map<String, dynamic> doc;
  final Map<String, dynamic> workflow;
}

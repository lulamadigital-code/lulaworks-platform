import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/status_pill.dart';
import 'pdf_viewer_screen.dart';

/// Tax invoices & delivery notes over one endpoint (?kind=). Invoices show money
/// (Golden-Rule gated); delivery notes show quantities only — the backend never
/// sends prices for them (§15), so there's nothing to leak here.
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
          scrolledUnderElevation: 1,
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

class _DocListState extends State<_DocList> with AutomaticKeepAliveClientMixin {
  late Future<List<Map<String, dynamic>>> _future = _load();

  @override
  bool get wantKeepAlive => true;

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('/commercial-documents/?kind=${widget.kind}'));

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final isInvoice = widget.kind == 'invoice';
    return RefreshIndicator(
      color: kBrand,
      onRefresh: () async => setState(() { _future = _load(); }),
      child: FutureBuilder<List<Map<String, dynamic>>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return const _CardsSkeleton();
          }
          if (snap.hasError) {
            return ListView(children: [
              const SizedBox(height: 120),
              const Icon(Icons.cloud_off, size: 44, color: kMuted),
              const SizedBox(height: 12),
              Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
            ]);
          }
          final rows = snap.data ?? const [];
          if (rows.isEmpty) {
            return ListView(children: [
              const SizedBox(height: 130),
              Icon(isInvoice ? Icons.receipt_long_outlined : Icons.local_shipping_outlined,
                  size: 46, color: kMuted),
              const SizedBox(height: 12),
              Center(
                  child: Text('No ${isInvoice ? 'invoices' : 'delivery notes'} yet.')),
            ]);
          }
          return ListView.builder(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
            itemCount: rows.length,
            itemBuilder: (context, i) => _DocCard(
              api: widget.api,
              row: rows[i],
              onReturn: () => setState(() { _future = _load(); }),
            ),
          );
        },
      ),
    );
  }
}

class _DocCard extends StatelessWidget {
  const _DocCard({required this.api, required this.row, required this.onReturn});
  final ApiClient api;
  final Map<String, dynamic> row;
  final VoidCallback onReturn;

  @override
  Widget build(BuildContext context) {
    final isInvoice = row['kind'] == 'invoice';
    final state = '${row['payment_state'] ?? ''}';
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: () async {
            final changed = await Navigator.of(context).push<bool>(MaterialPageRoute(
                builder: (_) => _DocDetail(api: api, docId: '${row['id']}')));
            if (changed == true) onReturn();
          },
          child: Container(
            decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: kLine)),
            padding: const EdgeInsets.all(15),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(
                  child: Text('${row['number']}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          fontSize: 15, fontWeight: FontWeight.w700, color: kInk)),
                ),
                const SizedBox(width: 8),
                StatusPill(status: '${row['status']}'),
              ]),
              const SizedBox(height: 3),
              Text('${row['client_name'] ?? row['quotation_number'] ?? ''}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 12.5, color: kMuted)),
              if (isInvoice) ...[
                const SizedBox(height: 8),
                Row(children: [
                  Text(api.money(row['total']),
                      style: const TextStyle(
                          fontSize: 15, fontWeight: FontWeight.w700, color: kBrandDark)),
                  const Spacer(),
                  if (state.isNotEmpty) _payBadge(state),
                ]),
              ],
            ]),
          ),
        ),
      ),
    );
  }

  Widget _payBadge(String state) {
    final (Color c, String label) = switch (state) {
      'paid' => (kGreen, 'Paid'),
      'part' => (kOrange, 'Part-paid'),
      _ => (kRed, 'Unpaid'),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(
          color: c.withOpacity(0.13), borderRadius: BorderRadius.circular(8)),
      child: Text(label,
          style: TextStyle(color: c, fontSize: 11.5, fontWeight: FontWeight.w600)),
    );
  }
}

// ── Detail ───────────────────────────────────────────────────────────────────
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
            scrolledUnderElevation: 1,
            leading: BackButton(onPressed: () => Navigator.pop(context, _changed)),
            actions: [
              if (doc != null && widget.api.canDownloadPdf)
                IconButton(
                  tooltip: 'View PDF',
                  icon: const Icon(Icons.picture_as_pdf_outlined),
                  onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => PdfViewerScreen(
                        api: widget.api,
                        path: '/commercial-documents/${widget.docId}/pdf/',
                        title: '${doc['number']}'),
                  )),
                ),
            ],
          ),
          body: doc == null
              ? const Center(child: CircularProgressIndicator(color: kBrand))
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
    return ListView(padding: const EdgeInsets.fromLTRB(20, 16, 20, 32), children: [
      _headerRow(context, doc),
      const SizedBox(height: 20),
      _label('LINE ITEMS'),
      const SizedBox(height: 10),
      _card(Column(children: [
        for (int i = 0; i < lines.length; i++) ...[
          if (i > 0) const Divider(height: 1),
          _priceLine(context, lines[i]),
        ],
        const Divider(height: 1),
        _amount(context, 'Invoice total', widget.api.money(doc['total']), bold: true),
        _amount(context, 'Paid', widget.api.money(doc['amount_paid'])),
        _amount(context, 'Outstanding', widget.api.money(doc['outstanding']),
            color: outstanding > 0 ? kRed : kGreen),
        const SizedBox(height: 6),
      ])),
      if (payments.isNotEmpty) ...[
        const SizedBox(height: 18),
        _label('PAYMENTS'),
        const SizedBox(height: 10),
        _card(Column(children: [
          for (int i = 0; i < payments.length; i++) ...[
            if (i > 0) const Divider(height: 1),
            ListTile(
              dense: true,
              leading: const Icon(Icons.payments_outlined, size: 20, color: kMuted),
              title: Text(widget.api.money(payments[i]['amount']),
                  style: const TextStyle(fontWeight: FontWeight.w600, color: kInk)),
              subtitle: Text('${payments[i]['date'] ?? ''}'
                  '${'${payments[i]['reference'] ?? ''}'.isNotEmpty ? ' · ${payments[i]['reference']}' : ''}',
                  style: const TextStyle(color: kMuted)),
            ),
          ],
        ])),
      ],
      if (outstanding > 0 && widget.api.canRecordPayment) ...[
        const SizedBox(height: 18),
        FilledButton.tonalIcon(
          onPressed: _recordPayment,
          icon: const Icon(Icons.add_card),
          label: const Text('Record payment'),
        ),
      ],
      _workflow(context, d),
    ]);
  }

  Widget _deliveryBody(BuildContext context, _Doc d) {
    final doc = d.doc;
    final lines = (doc['lines'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    return ListView(padding: const EdgeInsets.fromLTRB(20, 16, 20, 32), children: [
      _headerRow(context, doc),
      const SizedBox(height: 6),
      if ('${doc['delivery_address'] ?? ''}'.isNotEmpty)
        Text('Deliver to: ${doc['delivery_address']}',
            style: const TextStyle(fontSize: 13, color: kMuted)),
      if ('${doc['delivery_date'] ?? ''}'.isNotEmpty)
        Text('Date: ${doc['delivery_date']}',
            style: const TextStyle(fontSize: 13, color: kMuted)),
      const SizedBox(height: 18),
      _label('ITEMS DELIVERED  ·  ${lines.length}'),
      const SizedBox(height: 10),
      // Quantities only — a delivery note never shows prices (§15).
      _card(Column(children: [
        for (int i = 0; i < lines.length; i++) ...[
          if (i > 0) const Divider(height: 1),
          ListTile(
            dense: true,
            title: Text('${lines[i]['description'] ?? '—'}',
                style: const TextStyle(color: kInk)),
            trailing: Text('${lines[i]['qty']} ${lines[i]['unit'] ?? ''}',
                style: const TextStyle(fontWeight: FontWeight.w600, color: kInk)),
          ),
        ],
      ])),
      if ('${doc['delivery_notes'] ?? ''}'.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text('${doc['delivery_notes']}',
            style: const TextStyle(fontSize: 13, color: kInk)),
      ],
      _workflow(context, d),
    ]);
  }

  Widget _headerRow(BuildContext context, Map<String, dynamic> doc) {
    return Row(children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${doc['client_name'] ?? ''}',
              style: const TextStyle(
                  fontSize: 18, fontWeight: FontWeight.w700, color: kInk)),
          if ('${doc['quotation_number'] ?? ''}'.isNotEmpty)
            Text('From ${doc['quotation_number']}',
                style: const TextStyle(fontSize: 12.5, color: kMuted)),
        ]),
      ),
      const SizedBox(width: 8),
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
      _label('MOVE TO'),
      const SizedBox(height: 10),
      Wrap(spacing: 8, runSpacing: 8, children: [
        for (final n in next)
          FilledButton.tonal(
            onPressed: () => _transition('${n['value']}', '${n['label']}'),
            child: Text('${n['label']}'),
          ),
      ]),
    ]);
  }

  Widget _label(String s) => Text(s,
      style: const TextStyle(
          fontSize: 11.5, fontWeight: FontWeight.w700,
          letterSpacing: 0.6, color: kMuted));

  Widget _card(Widget child) => Container(
        decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: kLine)),
        padding: const EdgeInsets.symmetric(horizontal: 14),
        child: child,
      );

  Widget _priceLine(BuildContext context, Map<String, dynamic> l) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${l['description'] ?? '—'}',
                style: const TextStyle(fontSize: 13.5, color: kInk)),
            const SizedBox(height: 2),
            Text('${l['qty'] ?? ''} ${l['unit'] ?? ''} × ${widget.api.money(l['unit_price'])}',
                style: const TextStyle(fontSize: 12, color: kMuted)),
          ]),
        ),
        const SizedBox(width: 10),
        Text(widget.api.money(l['line_total']),
            style: const TextStyle(
                fontSize: 13.5, fontWeight: FontWeight.w600, color: kInk)),
      ]),
    );
  }

  Widget _amount(BuildContext context, String label, String value,
      {bool bold = false, Color? color}) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text(label,
            style: TextStyle(
                fontSize: bold ? 15 : 13,
                fontWeight: bold ? FontWeight.w700 : FontWeight.w400,
                color: bold ? kInk : kMuted)),
        Text(value,
            style: TextStyle(
                fontSize: bold ? 16 : 13.5,
                fontWeight: bold ? FontWeight.w700 : FontWeight.w500,
                color: color ?? (bold ? kBrandDark : kInk))),
      ]),
    );
  }
}

class _Doc {
  _Doc({required this.doc, required this.workflow});
  final Map<String, dynamic> doc;
  final Map<String, dynamic> workflow;
}

class _CardsSkeleton extends StatelessWidget {
  const _CardsSkeleton();
  @override
  Widget build(BuildContext context) {
    Widget card() => Container(
          margin: const EdgeInsets.only(bottom: 10),
          height: 92,
          decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: kLine)),
        );
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
      children: List.generate(5, (_) => card()),
    );
  }
}

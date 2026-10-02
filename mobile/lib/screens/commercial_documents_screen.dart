import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/related_records.dart';
import '../widgets/status_pill.dart';
import '../ui/lw_components.dart';
import 'business_history_screen.dart';
import 'invoice_form_screen.dart';
import 'lulaai_screen.dart';
import 'pdf_viewer_screen.dart';

/// Tax invoices & delivery notes over one endpoint (?kind=). Invoices show money
/// (Golden-Rule gated); delivery notes show quantities only — the backend never
/// sends prices for them (§15), so there's nothing to leak here.
class CommercialDocumentsScreen extends StatefulWidget {
  const CommercialDocumentsScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<CommercialDocumentsScreen> createState() =>
      _CommercialDocumentsScreenState();
}

class _CommercialDocumentsScreenState extends State<CommercialDocumentsScreen> {
  int _rev = 0; // bumped to force the invoice list to reload after a create

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        floatingActionButton: widget.api.can('quotes.create')
            ? FloatingActionButton.extended(
                onPressed: () async {
                  final created = await Navigator.of(context).push(
                      MaterialPageRoute(
                          builder: (_) => InvoiceFormScreen(api: widget.api)));
                  if (created != null && mounted) setState(() => _rev++);
                },
                icon: const Icon(Icons.add),
                label: const Text('New invoice'),
              )
            : null,
        appBar: AppBar(
          title: const Text('Invoices & delivery'),
          scrolledUnderElevation: 1,
          bottom: const TabBar(tabs: [
            Tab(text: 'Tax invoices'),
            Tab(text: 'Delivery notes'),
          ]),
        ),
        body: TabBarView(children: [
          _DocList(key: ValueKey('inv-$_rev'), api: widget.api, kind: 'invoice'),
          _DocList(api: widget.api, kind: 'delivery'),
        ]),
      ),
    );
  }
}

class _DocList extends StatefulWidget {
  const _DocList({super.key, required this.api, required this.kind});
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
              Icon(Icons.cloud_off, size: 44, color: kMuted),
              const SizedBox(height: 12),
              Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
            ]);
          }
          final rows = snap.data ?? const [];
          if (rows.isEmpty) {
            return LwEmptyState(
              icon: isInvoice ? Icons.receipt_long_outlined : Icons.local_shipping_outlined,
              title: isInvoice ? 'No invoices yet' : 'No delivery notes yet',
              message: isInvoice
                  ? 'Raise a tax invoice directly, or generate one from an '
                      'approved quotation.'
                  : 'Delivery notes generated from invoices will appear here.',
            );
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
                      style: TextStyle(
                          fontSize: 15, fontWeight: FontWeight.w700, color: kInk)),
                ),
                const SizedBox(width: 8),
                StatusPill(status: '${row['status']}'),
              ]),
              const SizedBox(height: 3),
              Text('${row['client_name'] ?? row['quotation_number'] ?? ''}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 12.5, color: kMuted)),
              if (isInvoice) ...[
                const SizedBox(height: 8),
                Row(children: [
                  Text(api.money(row['total']),
                      style: TextStyle(
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

/// Public opener for the (private) commercial-document detail — lets deep links
/// (notifications, search) jump straight to one invoice/delivery note.
Widget commercialDocumentDetailScreen(
        {required ApiClient api, required String docId}) =>
    _DocDetail(api: api, docId: docId);

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
    // getCached: the document renders from the last sync offline; workflow degrades.
    final results = await Future.wait([
      widget.api.getCached('/commercial-documents/$id/').then((c) => c.data),
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
    // One key per payment attempt — a lost-response retry reuses it, so the
    // backend records the payment once (never a double-charge).
    final idemKey = newIdempotencyKey();
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
      await widget.api.post('/commercial-documents/${widget.docId}/payment/', {
        'amount': amount.text.trim(),
        'reference': ref.text.trim(),
        'idempotency_key': idemKey,
      });
      _changed = true;
      setState(() { _future = _load(); });
      messenger.showSnackBar(const SnackBar(content: Text('Payment recorded')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  /// Email this invoice/delivery note (PDF attached) to the customer.
  Future<void> _send() async {
    final to = TextEditingController();
    final msg = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Send to customer'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          const Text('Emails the document with its PDF attached. Leave the '
              'address blank to use the customer’s contact.'),
          const SizedBox(height: 12),
          TextField(controller: to, decoration: const InputDecoration(
              labelText: 'To (optional)'), keyboardType: TextInputType.emailAddress),
          TextField(controller: msg, decoration: const InputDecoration(
              labelText: 'Message (optional)'), maxLines: 2),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Send')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      final res = await widget.api.post('/commercial-documents/${widget.docId}/send/',
          {'to': to.text.trim(), 'message': msg.text.trim()}) as Map;
      messenger.showSnackBar(SnackBar(
          content: Text('Sent to ${res['to'] ?? 'the customer'}')));
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
              // Primary (Send) visible; secondary actions in one overflow (§8/§10).
              if (doc != null && widget.api.canCreateQuote)
                IconButton(
                  tooltip: 'Send to customer',
                  icon: const Icon(Icons.send_outlined),
                  onPressed: _send,
                ),
              LwActionMenu(actions: [
                if (doc != null && widget.api.canDownloadPdf)
                  LwAction('View PDF', Icons.picture_as_pdf_outlined, () =>
                      Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => PdfViewerScreen(
                            api: widget.api,
                            path: '/commercial-documents/${widget.docId}/pdf/',
                            title: '${doc['number'] ?? ''}')))),
                LwAction('Business history', Icons.history, () =>
                    Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => BusinessHistoryScreen(
                          api: widget.api, kind: 'commercial_document', id: widget.docId,
                          title: '${doc?['number'] ?? 'Document'}')))),
                if (widget.api.canGenerateAi)
                  LwAction('Ask LulaAI', Icons.auto_awesome, () =>
                      Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => LulaAiScreen(
                            api: widget.api, ctxType: 'commercial_document', ctxId: widget.docId,
                            ctxLabel: 'document ${doc?['number'] ?? ''}')))),
              ]),
            ],
          ),
          body: doc == null
              ? Center(child: CircularProgressIndicator(color: kBrand))
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
              leading: Icon(Icons.payments_outlined, size: 20, color: kMuted),
              title: Text(widget.api.money(payments[i]['amount']),
                  style: TextStyle(fontWeight: FontWeight.w600, color: kInk)),
              subtitle: Text('${payments[i]['date'] ?? ''}'
                  '${'${payments[i]['reference'] ?? ''}'.isNotEmpty ? ' · ${payments[i]['reference']}' : ''}',
                  style: TextStyle(color: kMuted)),
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
      RelatedRecords(api: widget.api, type: 'commercial_document', id: widget.docId),
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
            style: TextStyle(fontSize: 13, color: kMuted)),
      if ('${doc['delivery_date'] ?? ''}'.isNotEmpty)
        Text('Date: ${doc['delivery_date']}',
            style: TextStyle(fontSize: 13, color: kMuted)),
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
                style: TextStyle(color: kInk)),
            trailing: Text('${lines[i]['qty']} ${lines[i]['unit'] ?? ''}',
                style: TextStyle(fontWeight: FontWeight.w600, color: kInk)),
          ),
        ],
      ])),
      if ('${doc['delivery_notes'] ?? ''}'.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text('${doc['delivery_notes']}',
            style: TextStyle(fontSize: 13, color: kInk)),
      ],
      _workflow(context, d),
      RelatedRecords(api: widget.api, type: 'commercial_document', id: widget.docId),
    ]);
  }

  Widget _headerRow(BuildContext context, Map<String, dynamic> doc) {
    return Row(children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${doc['client_name'] ?? ''}',
              style: TextStyle(
                  fontSize: 18, fontWeight: FontWeight.w700, color: kInk)),
          if ('${doc['quotation_number'] ?? ''}'.isNotEmpty)
            Text('From ${doc['quotation_number']}',
                style: TextStyle(fontSize: 12.5, color: kMuted)),
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
      style: TextStyle(
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
                style: TextStyle(fontSize: 13.5, color: kInk)),
            const SizedBox(height: 2),
            Text('${l['qty'] ?? ''} ${l['unit'] ?? ''} × ${widget.api.money(l['unit_price'])}',
                style: TextStyle(fontSize: 12, color: kMuted)),
          ]),
        ),
        const SizedBox(width: 10),
        Text(widget.api.money(l['line_total']),
            style: TextStyle(
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

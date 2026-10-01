import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../ui/lw_components.dart';
import '../widgets/related_records.dart';
import '../theme.dart';
import '../widgets/status_pill.dart';
import 'business_history_screen.dart';
import 'commercial_documents_screen.dart';
import 'lulaai_screen.dart';
import 'pdf_viewer_screen.dart';
import 'quotation_form_screen.dart';

/// Quotations — searchable card list → detail with line items, VAT and totals
/// (Golden-Rule gated), the status workflow, and the official PDF. Creating a
/// quote with line items stays on the web for now (§58).
class QuotationsScreen extends StatefulWidget {
  const QuotationsScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<QuotationsScreen> createState() => _QuotationsScreenState();
}

class _QuotationsScreenState extends State<QuotationsScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load('');
  final _search = TextEditingController();
  Timer? _debounce;

  Future<List<Map<String, dynamic>>> _load(String q) async {
    final path = q.trim().isEmpty
        ? '/quotations/'
        : '/quotations/?search=${Uri.encodeQueryComponent(q.trim())}';
    return pageResults(await widget.api.get(path));
  }

  void _onSearch(String q) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350),
        () => setState(() { _future = _load(q); }));
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      floatingActionButton: widget.api.can('quotes.create')
          ? FloatingActionButton.extended(
              onPressed: () async {
                final created = await Navigator.of(context).push(
                    MaterialPageRoute(
                        builder: (_) => QuotationFormScreen(api: widget.api)));
                if (created != null && mounted) {
                  setState(() => _future = _load(''));
                }
              },
              icon: const Icon(Icons.add),
              label: const Text('New'),
            )
          : null,
      appBar: AppBar(
        title: const Text('Quotations'),
        scrolledUnderElevation: 1,
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(58),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
            child: TextField(
              controller: _search,
              onChanged: _onSearch,
              decoration: InputDecoration(
                hintText: 'Search quotations',
                prefixIcon: const Icon(Icons.search, size: 20),
                isDense: true,
                filled: true,
                fillColor: kBg,
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: const BorderSide(color: kLine)),
                enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: const BorderSide(color: kLine)),
              ),
            ),
          ),
        ),
      ),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => setState(() { _future = _load(_search.text); }),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const _DocSkeleton();
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
              return LwEmptyState(
                icon: Icons.article_outlined,
                title: 'No quotations yet',
                message: 'Create a quotation and manage it from draft through '
                    'approval, PDF and sending — all in one place.',
                actionLabel: widget.api.canCreateQuote ? 'New quotation' : null,
                onAction: widget.api.canCreateQuote
                    ? () => Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => QuotationFormScreen(api: widget.api)))
                    : null,
              );
            }
            return ListView.builder(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
              itemCount: rows.length,
              itemBuilder: (context, i) => _QuoteCard(
                api: widget.api,
                row: rows[i],
                onReturn: () => setState(() { _future = _load(_search.text); }),
              ),
            );
          },
        ),
      ),
    );
  }
}

class _QuoteCard extends StatelessWidget {
  const _QuoteCard({required this.api, required this.row, required this.onReturn});
  final ApiClient api;
  final Map<String, dynamic> row;
  final VoidCallback onReturn;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: () async {
            final changed = await Navigator.of(context).push<bool>(MaterialPageRoute(
                builder: (_) =>
                    QuotationDetailScreen(api: api, quoteId: '${row['id']}')));
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
              Text([row['client_name'], row['title']]
                      .where((s) => '$s'.isNotEmpty).join('  ·  '),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 12.5, color: kMuted)),
              const SizedBox(height: 8),
              Text(api.money(row['total']),
                  style: const TextStyle(
                      fontSize: 15, fontWeight: FontWeight.w700, color: kBrandDark)),
            ]),
          ),
        ),
      ),
    );
  }
}

// ── Detail ───────────────────────────────────────────────────────────────────
class QuotationDetailScreen extends StatefulWidget {
  const QuotationDetailScreen({super.key, required this.api, required this.quoteId});
  final ApiClient api;
  final String quoteId;

  @override
  State<QuotationDetailScreen> createState() => _QuotationDetailScreenState();
}

class _QuotationDetailScreenState extends State<QuotationDetailScreen> {
  late Future<_QuoteDetail> _future = _load();
  bool _changed = false;

  Future<_QuoteDetail> _load() async {
    final id = widget.quoteId;
    // getCached: the quote itself renders from the last sync when offline; the
    // workflow (available next-actions) degrades to empty.
    final results = await Future.wait([
      widget.api.getCached('/quotations/$id/').then((c) => c.data),
      widget.api.get('/quotations/$id/workflow/').catchError((_) => null),
    ]);
    return _QuoteDetail(
      quote: (results[0] as Map).cast<String, dynamic>(),
      workflow: results[1] is Map ? (results[1] as Map).cast<String, dynamic>() : const {},
    );
  }

  Future<void> _transition(String toStatus, String label) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('/quotations/${widget.quoteId}/transition/',
          {'to_status': toStatus});
      _changed = true;
      setState(() { _future = _load(); });
      messenger.showSnackBar(SnackBar(content: Text('Moved to $label')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission for that."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  bool _genBusy = false;

  /// Raise a tax invoice / delivery note from this quotation (same backend
  /// service + rules as web). `kind` is the action: 'invoice' or 'delivery-note'.
  Future<void> _generate(String kind, String label) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _genBusy = true);
    try {
      final doc = await widget.api
          .post('/quotations/${widget.quoteId}/$kind/') as Map;
      _changed = true;
      if (!mounted) return;
      messenger.showSnackBar(
          SnackBar(content: Text('$label ${doc['number'] ?? ''} created')));
      await Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => CommercialDocumentsScreen(api: widget.api)));
      if (mounted) setState(() => _future = _load());
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission for that."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    } finally {
      if (mounted) setState(() => _genBusy = false);
    }
  }

  /// Email this quotation (PDF attached) to the customer — same backend service
  /// as web. Recipient defaults to the customer's contact; an override is optional.
  Future<void> _sendToCustomer() async {
    final to = TextEditingController();
    final msg = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Send to customer'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          const Text('Emails the quotation with its PDF attached. Leave the '
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
      final res = await widget.api.post('/quotations/${widget.quoteId}/send/',
          {'to': to.text.trim(), 'message': msg.text.trim()}) as Map;
      messenger.showSnackBar(SnackBar(
          content: Text('Sent to ${res['to'] ?? 'the customer'}')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<_QuoteDetail>(
      future: _future,
      builder: (context, snap) {
        final q = snap.data?.quote;
        return Scaffold(
          appBar: AppBar(
            title: Text('${q?['number'] ?? 'Quotation'}'),
            scrolledUnderElevation: 1,
            leading: BackButton(onPressed: () => Navigator.pop(context, _changed)),
            actions: [
              // Primary contextual action stays visible; everything secondary
              // collapses into one overflow menu (design system §8/§10).
              if (q != null && widget.api.canCreateQuote)
                IconButton(
                  tooltip: 'Send to customer',
                  icon: const Icon(Icons.send_outlined),
                  onPressed: _sendToCustomer,
                ),
              LwActionMenu(actions: [
                if (q != null && widget.api.canDownloadPdf)
                  LwAction('View PDF', Icons.picture_as_pdf_outlined, () =>
                      Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => PdfViewerScreen(
                            api: widget.api,
                            path: '/quotations/${widget.quoteId}/pdf/',
                            title: '${q['number'] ?? ''}'))) ),
                LwAction('Business history', Icons.history, () =>
                    Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => BusinessHistoryScreen(
                          api: widget.api, kind: 'quotation', id: widget.quoteId,
                          title: '${q?['number'] ?? 'Quotation'}'))) ),
                if (widget.api.canGenerateAi)
                  LwAction('Ask LulaAI', Icons.auto_awesome, () =>
                      Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => LulaAiScreen(
                            api: widget.api, ctxType: 'quotation', ctxId: widget.quoteId,
                            ctxLabel: 'quotation ${q?['number'] ?? ''}'))) ),
              ]),
            ],
          ),
          body: q == null
              ? const Center(child: CircularProgressIndicator(color: kBrand))
              : _body(context, snap.data!),
        );
      },
    );
  }

  Widget _body(BuildContext context, _QuoteDetail d) {
    final q = d.quote;
    final lines = (q['lines'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    final next = (d.workflow['next'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    final canMove = widget.api.canCreateQuote || widget.api.canApproveQuote;
    return ListView(padding: const EdgeInsets.fromLTRB(20, 16, 20, 32), children: [
      Row(children: [
        Expanded(
          child: Text('${q['client_name'] ?? ''}',
              style: const TextStyle(
                  fontSize: 19, fontWeight: FontWeight.w700, color: kInk)),
        ),
        const SizedBox(width: 8),
        StatusPill(status: '${q['status']}'),
      ]),
      const SizedBox(height: 4),
      Text([
        if ('${q['title'] ?? ''}'.isNotEmpty) '${q['title']}',
        if ('${q['site'] ?? ''}'.isNotEmpty) 'Site: ${q['site']}',
        if ('${q['validity_date'] ?? ''}'.isNotEmpty) 'Valid to ${q['validity_date']}',
      ].join('  ·  '), style: const TextStyle(fontSize: 12.5, color: kMuted)),
      const SizedBox(height: 20),
      Text('LINE ITEMS  ·  ${lines.length}',
          style: const TextStyle(
              fontSize: 11.5, fontWeight: FontWeight.w700,
              letterSpacing: 0.6, color: kMuted)),
      const SizedBox(height: 10),
      Container(
        decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: kLine)),
        padding: const EdgeInsets.symmetric(horizontal: 14),
        child: Column(children: [
          for (int i = 0; i < lines.length; i++) ...[
            if (i > 0) const Divider(height: 1),
            _lineRow(context, lines[i]),
          ],
          const Divider(height: 1),
          _totalRow(context, 'Subtotal', widget.api.money(q['subtotal'])),
          _totalRow(context, 'VAT (${q['vat_rate'] ?? 0}%)',
              widget.api.money(q['vat_amount'])),
          _totalRow(context, 'Total', widget.api.money(q['total']), bold: true),
          const SizedBox(height: 6),
        ]),
      ),
      if ('${q['notes'] ?? ''}'.isNotEmpty) ...[
        const SizedBox(height: 16),
        Text('${q['notes']}', style: const TextStyle(fontSize: 13, color: kInk)),
      ],
      if (canMove && next.isNotEmpty) ...[
        const SizedBox(height: 22),
        const Text('MOVE TO',
            style: TextStyle(
                fontSize: 11.5, fontWeight: FontWeight.w700,
                letterSpacing: 0.6, color: kMuted)),
        const SizedBox(height: 10),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final n in next)
            FilledButton.tonal(
              onPressed: () => _transition('${n['value']}', '${n['label']}'),
              child: Text('${n['label']}'),
            ),
        ]),
      ],
      if (widget.api.can('quotes.create')) ...[
        const SizedBox(height: 22),
        const Text('GENERATE',
            style: TextStyle(
                fontSize: 11.5, fontWeight: FontWeight.w700,
                letterSpacing: 0.6, color: kMuted)),
        const SizedBox(height: 10),
        Wrap(spacing: 8, runSpacing: 8, children: [
          OutlinedButton.icon(
              onPressed:
                  _genBusy ? null : () => _generate('invoice', 'Tax invoice'),
              icon: const Icon(Icons.receipt_long_outlined, size: 18),
              label: const Text('Tax invoice')),
          OutlinedButton.icon(
              onPressed: _genBusy
                  ? null
                  : () => _generate('delivery-note', 'Delivery note'),
              icon: const Icon(Icons.local_shipping_outlined, size: 18),
              label: const Text('Delivery note')),
        ]),
      ],
      RelatedRecords(api: widget.api, type: 'quotation', id: widget.quoteId),
    ]);
  }

  Widget _lineRow(BuildContext context, Map<String, dynamic> l) {
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

  Widget _totalRow(BuildContext context, String label, String value,
      {bool bold = false}) {
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
                color: bold ? kBrandDark : kInk)),
      ]),
    );
  }
}

class _QuoteDetail {
  _QuoteDetail({required this.quote, required this.workflow});
  final Map<String, dynamic> quote;
  final Map<String, dynamic> workflow;
}

class _DocSkeleton extends StatelessWidget {
  const _DocSkeleton();
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

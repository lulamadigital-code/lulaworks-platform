import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../widgets/status_pill.dart';
import 'pdf_viewer_screen.dart';

/// Quotations — searchable list → detail with line items, VAT and totals
/// (Golden-Rule gated), plus the status workflow. Creating a quote with line
/// items stays on the web for now (§58); mobile views and moves it along.
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
      appBar: AppBar(
        title: const Text('Quotations'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(60),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
            child: TextField(
              controller: _search,
              onChanged: _onSearch,
              decoration: InputDecoration(
                hintText: 'Search quotations',
                prefixIcon: const Icon(Icons.search),
                isDense: true,
                filled: true,
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: BorderSide.none),
              ),
            ),
          ),
        ),
      ),
      body: RefreshIndicator(
        onRefresh: () async => setState(() { _future = _load(_search.text); }),
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
                Center(child: Text('No quotations.')),
              ]);
            }
            return ListView.separated(
              itemCount: rows.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final q = rows[i];
                return ListTile(
                  title: Text('${q['number']}  ·  ${q['client_name'] ?? ''}',
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  subtitle: Text('${q['title'] ?? ''}',
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  trailing: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      StatusPill(status: '${q['status']}'),
                      const SizedBox(height: 4),
                      Text(widget.api.money(q['total']),
                          style: const TextStyle(
                              fontSize: 12.5, fontWeight: FontWeight.w600)),
                    ],
                  ),
                  isThreeLine: true,
                  onTap: () async {
                    final changed = await Navigator.of(context).push<bool>(
                        MaterialPageRoute(
                            builder: (_) => QuotationDetailScreen(
                                api: widget.api, quoteId: '${q['id']}')));
                    if (changed == true) {
                      setState(() { _future = _load(_search.text); });
                    }
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
    final results = await Future.wait([
      widget.api.get('/quotations/$id/'),
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

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<_QuoteDetail>(
      future: _future,
      builder: (context, snap) {
        final q = snap.data?.quote;
        return Scaffold(
          appBar: AppBar(
            title: Text('${q?['number'] ?? 'Quotation'}'),
            leading: BackButton(onPressed: () => Navigator.pop(context, _changed)),
            actions: [
              if (q != null && widget.api.canDownloadPdf)
                IconButton(
                  tooltip: 'View PDF',
                  icon: const Icon(Icons.picture_as_pdf_outlined),
                  onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => PdfViewerScreen(
                        api: widget.api,
                        path: '/quotations/${widget.quoteId}/pdf/',
                        title: '${q['number']}'),
                  )),
                ),
            ],
          ),
          body: q == null
              ? const Center(child: CircularProgressIndicator())
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
    return ListView(padding: const EdgeInsets.all(16), children: [
      Row(children: [
        Expanded(child: Text('${q['client_name'] ?? ''}',
            style: Theme.of(context).textTheme.titleMedium)),
        StatusPill(status: '${q['status']}'),
      ]),
      if ('${q['site'] ?? ''}'.isNotEmpty)
        Text('Site: ${q['site']}', style: Theme.of(context).textTheme.bodySmall),
      if ('${q['validity_date'] ?? ''}'.isNotEmpty)
        Text('Valid until ${q['validity_date']}',
            style: Theme.of(context).textTheme.bodySmall),
      const SizedBox(height: 16),
      Text('Line items (${lines.length})',
          style: Theme.of(context).textTheme.titleSmall),
      const SizedBox(height: 6),
      for (final l in lines)
        Card(
          margin: const EdgeInsets.only(bottom: 8),
          child: ListTile(
            dense: true,
            title: Text('${l['description'] ?? '—'}'),
            subtitle: Text('${l['qty'] ?? ''} ${l['unit'] ?? ''}'
                ' × ${widget.api.money(l['unit_price'])}'),
            trailing: Text(widget.api.money(l['line_total']),
                style: const TextStyle(fontWeight: FontWeight.w600)),
          ),
        ),
      const Divider(height: 24),
      _totalRow(context, 'Subtotal', widget.api.money(q['subtotal'])),
      _totalRow(context, 'VAT (${q['vat_rate'] ?? 0}%)',
          widget.api.money(q['vat_amount'])),
      _totalRow(context, 'Total', widget.api.money(q['total']), bold: true),
      if ('${q['notes'] ?? ''}'.isNotEmpty) ...[
        const SizedBox(height: 16),
        Text('Notes', style: Theme.of(context).textTheme.labelLarge),
        Text('${q['notes']}'),
      ],
      if (canMove && next.isNotEmpty) ...[
        const SizedBox(height: 24),
        Text('Move to', style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final n in next)
              FilledButton.tonal(
                onPressed: () => _transition('${n['value']}', '${n['label']}'),
                child: Text('${n['label']}'),
              ),
          ],
        ),
      ],
      const SizedBox(height: 24),
    ]);
  }

  Widget _totalRow(BuildContext context, String label, String value,
      {bool bold = false}) {
    final style = bold
        ? Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold)
        : Theme.of(context).textTheme.bodyMedium;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text(label, style: style),
        Text(value, style: style),
      ]),
    );
  }
}

class _QuoteDetail {
  _QuoteDetail({required this.quote, required this.workflow});
  final Map<String, dynamic> quote;
  final Map<String, dynamic> workflow;
}

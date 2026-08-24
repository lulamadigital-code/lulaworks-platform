import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/status_pill.dart';
import 'pdf_viewer_screen.dart';

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
              return ListView(children: const [
                SizedBox(height: 130),
                Icon(Icons.article_outlined, size: 46, color: kMuted),
                SizedBox(height: 12),
                Center(child: Text('No quotations.')),
              ]);
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
            scrolledUnderElevation: 1,
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

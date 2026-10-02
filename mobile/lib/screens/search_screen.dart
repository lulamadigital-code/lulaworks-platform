import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../nav/global_create.dart';
import '../theme.dart';
import '../ui/tokens.dart';
import 'customer_detail_screen.dart';
import 'lulaai_screen.dart';
import 'quotations_screen.dart' show QuotationDetailScreen;

/// Global search across the company's data — one debounced query to
/// GET /api/v1/search/?q=, which is permission-scoped server-side (each group is
/// only returned when the user may see it). Taps open the app's own screen for
/// the entity types that have an id-addressable detail (customer, quotation).
class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final _ctrl = TextEditingController();
  Timer? _debounce;
  bool _busy = false;
  String? _error;
  List<Map<String, dynamic>> _groups = const [];
  String _lastQuery = '';

  @override
  void dispose() {
    _debounce?.cancel();
    _ctrl.dispose();
    super.dispose();
  }

  void _onChanged(String v) {
    _debounce?.cancel();
    final q = v.trim();
    if (q.length < 2) {
      setState(() {
        _groups = const [];
        _error = null;
        _lastQuery = q;
      });
      return;
    }
    _debounce = Timer(const Duration(milliseconds: 350), () => _run(q));
  }

  Future<void> _run(String q) async {
    setState(() {
      _busy = true;
      _error = null;
      _lastQuery = q;
    });
    try {
      final body = await widget.api.get('/search/?q=${Uri.encodeQueryComponent(q)}');
      final groups = (body is Map && body['groups'] is List)
          ? (body['groups'] as List)
              .map((g) => (g as Map).cast<String, dynamic>())
              .toList()
          : <Map<String, dynamic>>[];
      if (mounted) setState(() => _groups = groups);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (_) {
      if (mounted) setState(() => _error = 'Could not reach the server.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  static const _navigable = {'customer', 'quotation'};

  IconData _iconFor(String type) => switch (type) {
        'customer' => Icons.contacts_outlined,
        'project' => Icons.work_outline,
        'job' => Icons.construction_outlined,
        'quotation' => Icons.article_outlined,
        'commercial_document' => Icons.receipt_long_outlined,
        'supplier' => Icons.local_shipping_outlined,
        _ => Icons.circle_outlined,
      };

  void _open(String type, String id) {
    Widget? screen;
    if (type == 'customer') {
      screen = CustomerDetailScreen(api: widget.api, customerId: id);
    } else if (type == 'quotation') {
      screen = QuotationDetailScreen(api: widget.api, quoteId: id);
    }
    if (screen != null) {
      Navigator.of(context).push(MaterialPageRoute(builder: (_) => screen!));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 0,
        title: TextField(
          controller: _ctrl,
          autofocus: true,
          textInputAction: TextInputAction.search,
          onChanged: _onChanged,
          decoration: const InputDecoration(
            hintText: 'Search or ask…',
            border: InputBorder.none,
          ),
        ),
        actions: [
          if (_ctrl.text.isNotEmpty)
            IconButton(
              tooltip: 'Clear',
              icon: const Icon(Icons.close),
              onPressed: () {
                _ctrl.clear();
                _onChanged('');
              },
            ),
        ],
      ),
      body: _body(),
    );
  }

  void _askLula([String? q]) {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => LulaAiScreen(
            api: widget.api,
            initialQuestion: (q != null && q.trim().length >= 2) ? q.trim() : null)));
  }

  Widget _body() {
    if (_busy) return const Center(child: CircularProgressIndicator());

    // Command landing (empty query): act, don't just search (§50).
    if (_lastQuery.length < 2) {
      return ListView(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
          child: Text('ACTIONS', style: LwType.section),
        ),
        ListTile(
          leading: Icon(Icons.add, color: kBrand),
          title: const Text('Create…'),
          subtitle: const Text('Quotation, invoice, customer, PO…'),
          onTap: () => showGlobalCreate(context, widget.api),
        ),
        if (widget.api.canGenerateAi)
          ListTile(
            leading: Icon(Icons.auto_awesome, color: kBrand),
            title: const Text('Ask LulaAI'),
            subtitle: const Text('Summaries, history, unpaid invoices…'),
            onTap: () => _askLula(),
          ),
        Padding(
          padding: const EdgeInsets.all(24),
          child: Text('Or type to search customers, quotes, jobs, invoices…',
              style: TextStyle(color: kMuted), textAlign: TextAlign.center),
        ),
      ]);
    }

    return ListView(
      children: [
        if (_error != null)
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(_error!, style: TextStyle(color: kRed)),
          ),
        // Always offer to ask LulaAI about whatever was typed.
        if (widget.api.canGenerateAi)
          ListTile(
            leading: Icon(Icons.auto_awesome, color: kBrand),
            title: Text('Ask LulaAI about “$_lastQuery”'),
            onTap: () => _askLula(_lastQuery),
          ),
        if (_error == null && _groups.isEmpty)
          Padding(
            padding: const EdgeInsets.all(24),
            child: Text('No records match “$_lastQuery”.',
                style: TextStyle(color: kMuted), textAlign: TextAlign.center),
          ),
        for (final g in _groups) ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
            child: Text('${g['label']}'.toUpperCase(),
                style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    letterSpacing: .5,
                    color: kMuted)),
          ),
          for (final raw in (g['items'] as List? ?? const []))
            _row((raw as Map).cast<String, dynamic>()),
          const Divider(height: 1),
        ],
        const SizedBox(height: 24),
      ],
    );
  }

  Widget _row(Map<String, dynamic> it) {
    final type = '${it['type']}';
    final nav = _navigable.contains(type);
    return ListTile(
      leading: Icon(_iconFor(type), color: kBrand, size: 22),
      title: Text('${it['title'] ?? ''}',
          maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: '${it['sub'] ?? ''}'.isEmpty
          ? null
          : Text('${it['sub']}', maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: nav ? Icon(Icons.chevron_right, size: 18, color: kMuted) : null,
      onTap: nav ? () => _open(type, '${it['id']}') : null,
    );
  }
}

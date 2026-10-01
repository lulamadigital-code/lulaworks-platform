import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import 'customer_detail_screen.dart';
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
            hintText: 'Search customers, quotes, jobs…',
            border: InputBorder.none,
          ),
        ),
        actions: [
          if (_ctrl.text.isNotEmpty)
            IconButton(
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

  Widget _body() {
    if (_busy) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(child: Text(_error!, style: const TextStyle(color: kRed)));
    }
    if (_lastQuery.length < 2) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Text('Type at least 2 characters to search.',
              style: TextStyle(color: kMuted), textAlign: TextAlign.center),
        ),
      );
    }
    if (_groups.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text('No results for “$_lastQuery”.',
              style: const TextStyle(color: kMuted), textAlign: TextAlign.center),
        ),
      );
    }
    return ListView(
      children: [
        for (final g in _groups) ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
            child: Text('${g['label']}'.toUpperCase(),
                style: const TextStyle(
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
      trailing: nav ? const Icon(Icons.chevron_right, size: 18, color: kMuted) : null,
      onTap: nav ? () => _open(type, '${it['id']}') : null,
    );
  }
}

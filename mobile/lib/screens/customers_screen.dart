import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import 'customer_detail_screen.dart';
import 'customer_form_screen.dart';

/// The customer database — searchable card list, tap through to detail. Anyone
/// doing commercial work can browse; only customers.manage can add or edit.
class CustomersScreen extends StatefulWidget {
  const CustomersScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<CustomersScreen> createState() => _CustomersScreenState();
}

class _CustomersScreenState extends State<CustomersScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load('');
  final _search = TextEditingController();
  Timer? _debounce;

  Future<List<Map<String, dynamic>>> _load(String q) async {
    final path = q.trim().isEmpty
        ? '/customers/'
        : '/customers/?search=${Uri.encodeQueryComponent(q.trim())}';
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

  Future<void> _create() async {
    final saved = await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => CustomerFormScreen(api: widget.api)));
    if (saved is Map && saved['id'] != null && mounted) {
      Navigator.of(context)
          .push(MaterialPageRoute(
              builder: (_) => CustomerDetailScreen(
                  api: widget.api, customerId: '${saved['id']}')))
          .then((_) => setState(() { _future = _load(_search.text); }));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Customers'),
        scrolledUnderElevation: 1,
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(58),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
            child: TextField(
              controller: _search,
              onChanged: _onSearch,
              decoration: InputDecoration(
                hintText: 'Search customers',
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
      floatingActionButton: widget.api.canManageCustomers
          ? FloatingActionButton.extended(
              onPressed: _create,
              icon: const Icon(Icons.add),
              label: const Text('Add'))
          : null,
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => setState(() { _future = _load(_search.text); }),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const _ListSkeleton();
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
                const SizedBox(height: 120),
                const Icon(Icons.contacts_outlined, size: 46, color: kMuted),
                const SizedBox(height: 12),
                Center(
                    child: Text(_search.text.isEmpty
                        ? 'No customers yet.'
                        : 'No customers match “${_search.text}”.')),
              ]);
            }
            return ListView.builder(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 88),
              itemCount: rows.length,
              itemBuilder: (context, i) => _CustomerCard(
                api: widget.api,
                row: rows[i],
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => CustomerDetailScreen(
                        api: widget.api, customerId: '${rows[i]['id']}'))),
              ),
            );
          },
        ),
      ),
    );
  }
}

class _CustomerCard extends StatelessWidget {
  const _CustomerCard({required this.api, required this.row, required this.onTap});
  final ApiClient api;
  final Map<String, dynamic> row;
  final VoidCallback onTap;

  String get _initials {
    final p = '${row['name']}'.trim().split(RegExp(r'\s+')).where((s) => s.isNotEmpty);
    if (p.isEmpty) return '?';
    return p.take(2).map((s) => s.characters.first.toUpperCase()).join();
  }

  @override
  Widget build(BuildContext context) {
    final loc = [row['city'], row['province']]
        .where((s) => '$s'.isNotEmpty)
        .join(', ');
    final status = '${row['status'] ?? ''}';
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: Container(
            decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: kLine)),
            padding: const EdgeInsets.all(14),
            child: Row(children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                    color: kBrandTint, borderRadius: BorderRadius.circular(12)),
                alignment: Alignment.center,
                child: Text(_initials,
                    style: const TextStyle(
                        color: kBrandDark, fontWeight: FontWeight.w700, fontSize: 15)),
              ),
              const SizedBox(width: 13),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('${row['name']}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          fontSize: 15, fontWeight: FontWeight.w600, color: kInk)),
                  const SizedBox(height: 2),
                  Text([if ('${row['code'] ?? ''}'.isNotEmpty) '${row['code']}',
                        if (loc.isNotEmpty) loc].join('  ·  '),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 12.5, color: kMuted)),
                ]),
              ),
              if (status.isNotEmpty && status != 'active') ...[
                const SizedBox(width: 8),
                Text(status,
                    style: const TextStyle(fontSize: 11.5, color: kMuted)),
              ],
              const Icon(Icons.chevron_right, size: 20, color: kMuted),
            ]),
          ),
        ),
      ),
    );
  }
}

class _ListSkeleton extends StatelessWidget {
  const _ListSkeleton();
  @override
  Widget build(BuildContext context) {
    Widget card() => Container(
          margin: const EdgeInsets.only(bottom: 10),
          height: 72,
          decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: kLine)),
          padding: const EdgeInsets.all(14),
          child: Row(children: [
            Container(width: 44, height: 44,
                decoration: BoxDecoration(color: kLine, borderRadius: BorderRadius.circular(12))),
            const SizedBox(width: 13),
            Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Container(width: 160, height: 14,
                        decoration: BoxDecoration(color: kLine, borderRadius: BorderRadius.circular(6))),
                    const SizedBox(height: 8),
                    Container(width: 110, height: 11,
                        decoration: BoxDecoration(color: kLine, borderRadius: BorderRadius.circular(6))),
                  ]),
            ),
          ]),
        );
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
      children: List.generate(6, (_) => card()),
    );
  }
}

import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import 'customer_detail_screen.dart';
import 'customer_form_screen.dart';

/// The customer database — searchable list, tap through to detail. Anyone doing
/// commercial work can browse; only customers.manage can add or edit (the Add
/// button hides otherwise, and the backend enforces it).
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
    _debounce = Timer(const Duration(milliseconds: 350), () {
      setState(() { _future = _load(q); });
    });
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    final saved = await Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => CustomerFormScreen(api: widget.api),
    ));
    if (saved is Map && saved['id'] != null && mounted) {
      Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => CustomerDetailScreen(api: widget.api, customerId: '${saved['id']}'),
      )).then((_) => setState(() { _future = _load(_search.text); }));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Customers'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(60),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
            child: TextField(
              controller: _search,
              onChanged: _onSearch,
              decoration: InputDecoration(
                hintText: 'Search customers',
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
      floatingActionButton: widget.api.canManageCustomers
          ? FloatingActionButton.extended(
              onPressed: _create,
              icon: const Icon(Icons.add),
              label: const Text('Add'))
          : null,
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
              return ListView(children: [
                const SizedBox(height: 120),
                Center(
                    child: Text(_search.text.isEmpty
                        ? 'No customers yet.'
                        : 'No customers match “${_search.text}”.')),
              ]);
            }
            return ListView.separated(
              itemCount: rows.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) => _CustomerTile(
                row: rows[i],
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => CustomerDetailScreen(
                      api: widget.api, customerId: '${rows[i]['id']}'),
                )),
              ),
            );
          },
        ),
      ),
    );
  }
}

class _CustomerTile extends StatelessWidget {
  const _CustomerTile({required this.row, required this.onTap});
  final Map<String, dynamic> row;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final loc = [row['city'], row['province']]
        .where((s) => '$s'.isNotEmpty)
        .join(', ');
    return ListTile(
      leading: CircleAvatar(
        backgroundColor: scheme.primary.withOpacity(0.12),
        child: Text(_initials('${row['name']}'),
            style: TextStyle(color: scheme.primary, fontWeight: FontWeight.bold)),
      ),
      title: Text('${row['name']}', maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text([
        if ('${row['code'] ?? ''}'.isNotEmpty) '${row['code']}',
        if (loc.isNotEmpty) loc,
      ].join(' · '), maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: '${row['status']}' == 'active'
          ? null
          : Text('${row['status'] ?? ''}',
              style: TextStyle(fontSize: 12, color: scheme.outline)),
      onTap: onTap,
    );
  }

  String _initials(String s) {
    final p = s.trim().split(RegExp(r'\s+')).where((x) => x.isNotEmpty).toList();
    if (p.isEmpty) return '?';
    if (p.length == 1) return p.first.characters.first.toUpperCase();
    return (p.first.characters.first + p.last.characters.first).toUpperCase();
  }
}

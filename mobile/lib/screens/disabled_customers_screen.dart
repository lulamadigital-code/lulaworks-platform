import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../ui/lw_components.dart';

/// The tenant's disabled (soft-deleted) customers — the mobile equivalent of
/// the web "Customers → Disabled" view. A company admin (customers.manage) can
/// RESTORE one; only the software owner (platform owner/admin) can PERMANENTLY
/// delete it, and only from here — so a purge is always the deliberate second
/// step after a disable, never a one-tap destruction of a live customer.
class DisabledCustomersScreen extends StatefulWidget {
  const DisabledCustomersScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<DisabledCustomersScreen> createState() =>
      _DisabledCustomersScreenState();
}

class _DisabledCustomersScreenState extends State<DisabledCustomersScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('/customers/disabled/'));

  void _reload() => setState(() => _future = _load());

  Future<void> _restore(Map<String, dynamic> c) async {
    final name = '${c['name'] ?? c['code'] ?? 'this customer'}';
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('/customers/${c['id']}/restore/');
      messenger.showSnackBar(SnackBar(content: Text('$name restored')));
      _reload();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  Future<void> _purge(Map<String, dynamic> c) async {
    final name = '${c['name'] ?? c['code'] ?? 'this customer'}';
    final ok = await lwConfirm(
      context,
      title: 'Permanently delete?',
      message: '“$name” and its record will be removed for good. This cannot be '
          'undone. Only do this if the customer was created in error.',
      confirmLabel: 'Delete forever',
      destructive: true,
    );
    if (!ok || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.delete('/customers/${c['id']}/purge/');
      messenger.showSnackBar(SnackBar(content: Text('$name permanently deleted')));
      _reload();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.isForbidden
          ? 'Only the software owner can permanently delete a customer.'
          : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Disabled customers'), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => _reload(),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return LwErrorState(onRetry: _reload);
            }
            final rows = snap.data ?? const [];
            if (rows.isEmpty) {
              return ListView(children: const [
                SizedBox(height: 120),
                LwEmptyState(
                  icon: Icons.inventory_2_outlined,
                  title: 'No disabled customers',
                  message: 'Customers you disable appear here, where they can be '
                      'restored or permanently removed.',
                ),
              ]);
            }
            return ListView.separated(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
              itemCount: rows.length,
              separatorBuilder: (_, __) => const SizedBox(height: 10),
              itemBuilder: (context, i) => _DisabledCard(
                row: rows[i],
                canPurge: widget.api.isPlatformAdmin,
                onRestore: () => _restore(rows[i]),
                onPurge: () => _purge(rows[i]),
              ),
            );
          },
        ),
      ),
    );
  }
}

class _DisabledCard extends StatelessWidget {
  const _DisabledCard({
    required this.row,
    required this.canPurge,
    required this.onRestore,
    required this.onPurge,
  });
  final Map<String, dynamic> row;
  final bool canPurge;
  final VoidCallback onRestore;
  final VoidCallback onPurge;

  @override
  Widget build(BuildContext context) {
    final loc = [row['city'], row['province']]
        .where((s) => '$s'.isNotEmpty)
        .join(', ');
    return Container(
      decoration: BoxDecoration(
          color: kSurface,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: kLine)),
      padding: const EdgeInsets.fromLTRB(14, 12, 8, 8),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${row['name']}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: kInk)),
              if ('${row['code'] ?? ''}'.isNotEmpty || loc.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text([if ('${row['code'] ?? ''}'.isNotEmpty) '${row['code']}',
                      if (loc.isNotEmpty) loc].join('  ·  '),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 12.5, color: kMuted)),
              ],
            ]),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(
                color: kBg, borderRadius: BorderRadius.circular(20)),
            child: Text('Disabled',
                style: TextStyle(fontSize: 11, color: kMuted, fontWeight: FontWeight.w600)),
          ),
        ]),
        const Divider(height: 18),
        Row(mainAxisAlignment: MainAxisAlignment.end, children: [
          if (canPurge)
            TextButton.icon(
              onPressed: onPurge,
              icon: Icon(Icons.delete_forever_outlined, size: 18, color: kRed),
              label: Text('Delete forever', style: TextStyle(color: kRed)),
            ),
          const SizedBox(width: 4),
          FilledButton.tonalIcon(
            onPressed: onRestore,
            icon: const Icon(Icons.restore, size: 18),
            label: const Text('Restore'),
          ),
        ]),
      ]),
    );
  }
}

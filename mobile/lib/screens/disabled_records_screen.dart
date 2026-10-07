import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../ui/lw_components.dart';

/// A reusable "Disabled (soft-deleted) records" view — used for customers and
/// suppliers alike. A tenant admin can RESTORE a record; only the software owner
/// (platform owner/admin) can PERMANENTLY delete it, and only from here — so a
/// purge is always the deliberate second step after a disable.
///
/// [collectionPath] is the resource root, e.g. '/customers' or '/suppliers':
/// the screen reads `$collectionPath/disabled/`, posts to
/// `$collectionPath/<id>/restore/` and deletes `$collectionPath/<id>/purge/`.
class DisabledRecordsScreen extends StatefulWidget {
  const DisabledRecordsScreen({
    super.key,
    required this.api,
    required this.title,
    required this.collectionPath,
    required this.noun,
    required this.icon,
    this.subtitle,
  });

  final ApiClient api;
  final String title;
  final String collectionPath;
  final String noun; // singular, lower-case: 'customer', 'supplier'
  final IconData icon;
  final String Function(Map<String, dynamic> row)? subtitle;

  @override
  State<DisabledRecordsScreen> createState() => _DisabledRecordsScreenState();
}

class _DisabledRecordsScreenState extends State<DisabledRecordsScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('${widget.collectionPath}/disabled/'));

  void _reload() => setState(() => _future = _load());

  String _nameOf(Map<String, dynamic> r) =>
      '${r['name'] ?? r['code'] ?? 'this ${widget.noun}'}';

  Future<void> _restore(Map<String, dynamic> r) async {
    final name = _nameOf(r);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('${widget.collectionPath}/${r['id']}/restore/');
      messenger.showSnackBar(SnackBar(content: Text('$name restored')));
      _reload();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  Future<void> _purge(Map<String, dynamic> r) async {
    final name = _nameOf(r);
    final ok = await lwConfirm(
      context,
      title: 'Permanently delete?',
      message: '“$name” and its record will be removed for good. This cannot be '
          'undone. Only do this if the ${widget.noun} was created in error.',
      confirmLabel: 'Delete forever',
      destructive: true,
    );
    if (!ok || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.delete('${widget.collectionPath}/${r['id']}/purge/');
      messenger.showSnackBar(SnackBar(content: Text('$name permanently deleted')));
      _reload();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.isForbidden
          ? 'Only the software owner can permanently delete a ${widget.noun}.'
          : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.title), scrolledUnderElevation: 1),
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
              return ListView(children: [
                const SizedBox(height: 120),
                LwEmptyState(
                  icon: widget.icon,
                  title: 'No disabled ${widget.noun}s',
                  message: '${widget.noun[0].toUpperCase()}${widget.noun.substring(1)}s '
                      'you disable appear here, where they can be restored or '
                      'permanently removed.',
                ),
              ]);
            }
            return ListView.separated(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
              itemCount: rows.length,
              separatorBuilder: (_, __) => const SizedBox(height: 10),
              itemBuilder: (context, i) => _DisabledCard(
                title: _nameOf(rows[i]),
                subtitle: widget.subtitle?.call(rows[i]) ?? '',
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
    required this.title,
    required this.subtitle,
    required this.canPurge,
    required this.onRestore,
    required this.onPurge,
  });
  final String title;
  final String subtitle;
  final bool canPurge;
  final VoidCallback onRestore;
  final VoidCallback onPurge;

  @override
  Widget build(BuildContext context) {
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
              Text(title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: kInk)),
              if (subtitle.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(subtitle,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 12.5, color: kMuted)),
              ],
            ]),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(color: kBg, borderRadius: BorderRadius.circular(20)),
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

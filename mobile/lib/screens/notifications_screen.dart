import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';

/// In-app notifications for the signed-in user. Tapping an unread one marks it
/// read; the app-bar action clears them all. The unread count drives the bell
/// badge on the Home dashboard.
class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('/notifications/'));

  Future<void> _markRead({List<String>? ids}) async {
    try {
      await widget.api.post('/notifications/mark-read/',
          ids == null ? {} : {'ids': ids});
      setState(() { _future = _load(); });
    } catch (_) {/* best-effort */}
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Notifications'),
        actions: [
          TextButton(
            onPressed: () => _markRead(),
            child: const Text('Mark all read'),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async => setState(() { _future = _load(); }),
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
                SizedBox(height: 140),
                Icon(Icons.notifications_none, size: 48, color: Colors.grey),
                SizedBox(height: 12),
                Center(child: Text("You're all caught up.")),
              ]);
            }
            return ListView.separated(
              itemCount: rows.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final n = rows[i];
                final unread = n['is_read'] != true;
                final when = DateTime.tryParse('${n['created_at']}');
                return ListTile(
                  leading: Icon(
                    unread ? Icons.circle : Icons.circle_outlined,
                    size: 12,
                    color: unread
                        ? Theme.of(context).colorScheme.primary
                        : Theme.of(context).colorScheme.outline,
                  ),
                  title: Text('${n['title']}',
                      style: TextStyle(
                          fontWeight:
                              unread ? FontWeight.w600 : FontWeight.normal)),
                  subtitle: Text([
                    if ('${n['body'] ?? ''}'.isNotEmpty) '${n['body']}',
                    if (when != null) '${when.day}/${when.month}/${when.year}',
                  ].join('\n')),
                  isThreeLine: '${n['body'] ?? ''}'.isNotEmpty,
                  onTap: unread ? () => _markRead(ids: ['${n['id']}']) : null,
                );
              },
            );
          },
        ),
      ),
    );
  }
}

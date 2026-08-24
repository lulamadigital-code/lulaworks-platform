import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import 'task_chat_screen.dart';

/// The global chat inbox — every task thread the user is part of, newest first,
/// with a last-message preview and an unread badge. Tapping opens the thread.
class ChatInboxScreen extends StatefulWidget {
  const ChatInboxScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<ChatInboxScreen> createState() => _ChatInboxScreenState();
}

class _ChatInboxScreenState extends State<ChatInboxScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async {
    final r = await widget.api.get('/task-messages/inbox/');
    if (r is Map && r['threads'] is List) {
      return (r['threads'] as List).cast<Map<String, dynamic>>();
    }
    return [];
  }

  void _reload() => setState(() { _future = _load(); });

  void _open(Map<String, dynamic> t) async {
    await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => TaskChatScreen(
            api: widget.api,
            taskId: '${t['task_id']}',
            taskName: '${t['task_name']}')));
    _reload(); // unread clears after opening
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Messages'), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => _reload(),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator(color: kBrand));
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 120),
                const Icon(Icons.cloud_off, size: 44, color: kMuted),
                const SizedBox(height: 12),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
              ]);
            }
            final threads = snap.data ?? const [];
            if (threads.isEmpty) {
              return ListView(children: const [
                SizedBox(height: 140),
                Icon(Icons.forum_outlined, size: 48, color: kMuted),
                SizedBox(height: 12),
                Center(child: Text('No conversations yet',
                    style: TextStyle(fontSize: 15.5, fontWeight: FontWeight.w600, color: kInk))),
                SizedBox(height: 2),
                Center(child: Text('Task chats you join appear here.',
                    style: TextStyle(fontSize: 13, color: kMuted))),
              ]);
            }
            return ListView.separated(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: threads.length,
              separatorBuilder: (_, __) => const Divider(height: 1, indent: 72),
              itemBuilder: (context, i) => _row(context, threads[i]),
            );
          },
        ),
      ),
    );
  }

  Widget _row(BuildContext context, Map<String, dynamic> t) {
    final last = (t['last_message'] as Map?)?.cast<String, dynamic>() ?? const {};
    final unread = (t['unread'] as int? ?? 0);
    final system = last['is_system'] == true;
    final author = '${last['author_name'] ?? ''}'.trim();
    final preview = system
        ? '${last['body']}'
        : (author.isEmpty ? '${last['body']}' : '$author: ${last['body']}');
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      leading: Container(
        width: 44, height: 44,
        decoration: BoxDecoration(
            color: kBrandTint, borderRadius: BorderRadius.circular(12)),
        child: const Icon(Icons.forum_outlined, color: kBrandDark),
      ),
      title: Text('${t['task_name']}',
          maxLines: 1, overflow: TextOverflow.ellipsis,
          style: TextStyle(
              fontSize: 15,
              fontWeight: unread > 0 ? FontWeight.w700 : FontWeight.w600,
              color: kInk)),
      subtitle: Text(preview,
          maxLines: 1, overflow: TextOverflow.ellipsis,
          style: TextStyle(
              fontSize: 13,
              fontStyle: system ? FontStyle.italic : FontStyle.normal,
              color: unread > 0 ? kInk : kMuted)),
      trailing: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
        Text(_time(last['created_at']),
            style: const TextStyle(fontSize: 11.5, color: kMuted)),
        const SizedBox(height: 6),
        if (unread > 0)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
            decoration: const BoxDecoration(
                color: kBrand, shape: BoxShape.rectangle,
                borderRadius: BorderRadius.all(Radius.circular(10))),
            child: Text('$unread',
                style: const TextStyle(
                    fontSize: 11, color: Colors.white, fontWeight: FontWeight.w700)),
          )
        else
          const SizedBox(height: 16),
      ]),
      onTap: () => _open(t),
    );
  }

  String _time(dynamic iso) {
    final t = DateTime.tryParse('$iso')?.toLocal();
    if (t == null) return '';
    final now = DateTime.now();
    if (t.year == now.year && t.month == now.month && t.day == now.day) {
      return '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
    }
    return '${t.day}/${t.month}';
  }
}

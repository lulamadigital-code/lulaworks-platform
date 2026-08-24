import 'dart:async';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../api/api_client.dart';
import '../api/chat_store.dart';
import '../theme.dart';

/// The task's chat — business-context conversation between the people on a job.
/// Access is enforced server-side (participants + managers). Professional, not
/// WhatsApp: system events sit inline as an operational history. Sends survive
/// no-signal via a local outbox; the thread polls while open.
class TaskChatScreen extends StatefulWidget {
  const TaskChatScreen(
      {super.key, required this.api, required this.taskId, required this.taskName});
  final ApiClient api;
  final String taskId;
  final String taskName;

  @override
  State<TaskChatScreen> createState() => _TaskChatScreenState();
}

class _TaskChatScreenState extends State<TaskChatScreen> {
  final _store = ChatStore();
  final _input = TextEditingController();
  final _scroll = ScrollController();
  List<Map<String, dynamic>> _messages = [];
  List<String> _pending = [];
  bool _loading = true;
  bool _sending = false;
  Timer? _poll;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load(initial: true);
    _poll = Timer.periodic(const Duration(seconds: 10), (_) => _load());
  }

  @override
  void dispose() {
    _poll?.cancel();
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load({bool initial = false}) async {
    try {
      await _store.flush(widget.api, widget.taskId);
      final r = await widget.api.get('/task-messages/?task=${widget.taskId}');
      final msgs = _results(r);
      final pend = await _store.pending(widget.taskId);
      if (!mounted) return;
      final grew = msgs.length != _messages.length || pend.length != _pending.length;
      setState(() {
        _messages = msgs;
        _pending = pend;
        _loading = false;
        _error = null;
      });
      if (grew) _toBottom();
      // Clear this thread's unread for the inbox (best-effort).
      if (msgs.isNotEmpty) {
        widget.api.post('/task-messages/mark_read/', {'task': widget.taskId})
            .catchError((_) => null);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        if (initial) _error = '$e';
      });
    }
  }

  List<Map<String, dynamic>> _results(dynamic r) {
    if (r is Map && r['results'] is List) {
      return (r['results'] as List).cast<Map<String, dynamic>>();
    }
    if (r is List) return r.cast<Map<String, dynamic>>();
    return [];
  }

  void _toBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(_scroll.position.maxScrollExtent,
            duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      }
    });
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty || _sending) return;
    _input.clear();
    setState(() => _sending = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      try {
        await widget.api.post('/task-messages/', {'task': widget.taskId, 'body': text});
      } catch (e) {
        if (e is ApiException) rethrow;
        await _store.enqueue(widget.taskId, text); // offline → outbox
      }
      await _load();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You're not a participant on this task."
              : e.message)));
      _input.text = text; // let them retry
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  Future<void> _sendPhoto() async {
    final x = await ImagePicker().pickImage(source: ImageSource.camera, imageQuality: 70);
    if (x == null || !mounted) return;
    setState(() => _sending = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.postMultipart('/task-messages/',
          fields: {'task': widget.taskId, 'body': _input.text.trim()},
          filePath: x.path, fileField: 'image');
      _input.clear();
      await _load();
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not send photo — try again.')));
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 0,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Text('Task chat',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
            Text(widget.taskName,
                maxLines: 1, overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12, color: kMuted)),
          ],
        ),
        scrolledUnderElevation: 1,
      ),
      body: Column(children: [
        Expanded(child: _body()),
        _composer(),
      ]),
    );
  }

  Widget _body() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator(color: kBrand));
    }
    if (_error != null && _messages.isEmpty) {
      return ListView(children: [
        const SizedBox(height: 120),
        const Icon(Icons.cloud_off, size: 44, color: kMuted),
        const SizedBox(height: 12),
        Center(child: Text(_error!, textAlign: TextAlign.center)),
      ]);
    }
    if (_messages.isEmpty && _pending.isEmpty) {
      return ListView(children: const [
        SizedBox(height: 140),
        Icon(Icons.forum_outlined, size: 46, color: kMuted),
        SizedBox(height: 12),
        Center(child: Text('No messages yet',
            style: TextStyle(fontSize: 15.5, fontWeight: FontWeight.w600, color: kInk))),
        SizedBox(height: 2),
        Center(child: Text('Start the conversation for this task.',
            style: TextStyle(fontSize: 13, color: kMuted))),
      ]);
    }
    final me = widget.api.userId;
    return ListView(
      controller: _scroll,
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 14),
      children: [
        for (final m in _messages) _bubble(m, me),
        for (final p in _pending) _pendingBubble(p),
      ],
    );
  }

  Widget _bubble(Map<String, dynamic> m, String me) {
    if (m['is_system'] == true) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Center(
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
                color: kLine, borderRadius: BorderRadius.circular(20)),
            child: Text('${m['body']}',
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 12, color: kMuted)),
          ),
        ),
      );
    }
    final mine = '${m['author_id']}' == me;
    final img = m['image'];
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.78),
        padding: const EdgeInsets.fromLTRB(13, 9, 13, 9),
        decoration: BoxDecoration(
          color: mine ? kBrand : Colors.white,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(14),
            topRight: const Radius.circular(14),
            bottomLeft: Radius.circular(mine ? 14 : 4),
            bottomRight: Radius.circular(mine ? 4 : 14),
          ),
          border: mine ? null : Border.all(color: kLine),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (!mine && '${m['author_name']}'.trim().isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text('${m['author_name']}',
                    style: const TextStyle(
                        fontSize: 11.5, fontWeight: FontWeight.w700, color: kBrandDark)),
              ),
            if (img != null && '$img'.isNotEmpty) ...[
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.network('$img',
                    width: 200, fit: BoxFit.cover,
                    errorBuilder: (_, __, ___) => const SizedBox.shrink()),
              ),
              if ('${m['body']}'.trim().isNotEmpty) const SizedBox(height: 6),
            ],
            if ('${m['body']}'.trim().isNotEmpty)
              Text('${m['body']}',
                  style: TextStyle(
                      fontSize: 14.5, color: mine ? Colors.white : kInk, height: 1.3)),
            const SizedBox(height: 3),
            Text(_time(m['created_at']),
                style: TextStyle(
                    fontSize: 10.5,
                    color: mine ? Colors.white70 : kMuted)),
          ],
        ),
      ),
    );
  }

  Widget _pendingBubble(String body) => Align(
        alignment: Alignment.centerRight,
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 4),
          constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.78),
          padding: const EdgeInsets.fromLTRB(13, 9, 13, 9),
          decoration: BoxDecoration(
              color: kBrand.withOpacity(0.55),
              borderRadius: BorderRadius.circular(14)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
            Text(body, style: const TextStyle(fontSize: 14.5, color: Colors.white)),
            const SizedBox(height: 3),
            const Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(Icons.schedule, size: 11, color: Colors.white70),
              SizedBox(width: 3),
              Text('Sending…', style: TextStyle(fontSize: 10.5, color: Colors.white70)),
            ]),
          ]),
        ),
      );

  Widget _composer() {
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(8, 8, 8, 8),
        decoration: const BoxDecoration(
            color: Colors.white,
            border: Border(top: BorderSide(color: kLine))),
        child: Row(children: [
          IconButton(
            onPressed: _sending ? null : _sendPhoto,
            icon: const Icon(Icons.add_a_photo_outlined, color: kBrandDark),
          ),
          Expanded(
            child: TextField(
              controller: _input,
              minLines: 1,
              maxLines: 4,
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => _send(),
              decoration: InputDecoration(
                hintText: 'Message…',
                filled: true,
                fillColor: kBg,
                contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(22),
                    borderSide: BorderSide.none),
              ),
            ),
          ),
          const SizedBox(width: 6),
          Material(
            color: kBrand,
            shape: const CircleBorder(),
            child: InkWell(
              customBorder: const CircleBorder(),
              onTap: _sending ? null : _send,
              child: Padding(
                padding: const EdgeInsets.all(11),
                child: _sending
                    ? const SizedBox(width: 20, height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : const Icon(Icons.send, color: Colors.white, size: 20),
              ),
            ),
          ),
        ]),
      ),
    );
  }

  String _time(dynamic iso) {
    final t = DateTime.tryParse('$iso')?.toLocal();
    if (t == null) return '';
    return '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
  }
}

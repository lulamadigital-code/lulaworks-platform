import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

/// Help & Support — log a support ticket (a "call") and follow the conversation.
/// Tickets go into the same support system the web platform desk works from.
class HelpSupportScreen extends StatefulWidget {
  const HelpSupportScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<HelpSupportScreen> createState() => _HelpSupportScreenState();
}

class _HelpSupportScreenState extends State<HelpSupportScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async {
    final r = await widget.api.get('/support-tickets/');
    if (r is Map && r['results'] is List) {
      return (r['results'] as List).cast<Map<String, dynamic>>();
    }
    if (r is List) return r.cast<Map<String, dynamic>>();
    return [];
  }

  void _reload() => setState(() { _future = _load(); });

  Future<void> _open(Map<String, dynamic> t) async {
    await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => SupportTicketScreen(api: widget.api, ticket: t)));
    _reload();
  }

  Future<void> _newTicket() async {
    final created = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
        child: _NewTicketSheet(api: widget.api),
      ),
    );
    if (created == true) _reload();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Help & support'), scrolledUnderElevation: 1),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _newTicket,
        icon: const Icon(Icons.add_comment_outlined),
        label: const Text('New request'),
      ),
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
            final tickets = snap.data ?? const [];
            if (tickets.isEmpty) {
              return ListView(children: const [
                SizedBox(height: 130),
                Icon(Icons.support_agent_outlined, size: 48, color: kMuted),
                SizedBox(height: 12),
                Center(child: Text('No requests yet',
                    style: TextStyle(fontSize: 15.5, fontWeight: FontWeight.w600, color: kInk))),
                SizedBox(height: 2),
                Center(child: Text('Tap “New request” to log a support ticket.',
                    style: TextStyle(fontSize: 13, color: kMuted))),
              ]);
            }
            return ListView.separated(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 90),
              itemCount: tickets.length,
              separatorBuilder: (_, __) => const SizedBox(height: 9),
              itemBuilder: (context, i) => _ticketCard(context, tickets[i]),
            );
          },
        ),
      ),
    );
  }

  Widget _ticketCard(BuildContext context, Map<String, dynamic> t) {
    final (c, label) = statusStyle('${t['status']}');
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(13),
      child: InkWell(
        borderRadius: BorderRadius.circular(13),
        onTap: () => _open(t),
        child: Container(
          decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(13),
              border: Border.all(color: kLine)),
          padding: const EdgeInsets.fromLTRB(14, 12, 12, 12),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(
                child: Text('${t['subject']}',
                    maxLines: 2, overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 15, fontWeight: FontWeight.w600, color: kInk)),
              ),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                decoration: BoxDecoration(
                    color: c.withOpacity(0.13), borderRadius: BorderRadius.circular(8)),
                child: Text(label,
                    style: TextStyle(color: c, fontSize: 11.5, fontWeight: FontWeight.w700)),
              ),
            ]),
            const SizedBox(height: 4),
            Text([
              '${t['number']}',
              '${t['category_display'] ?? ''}',
              if ((t['message_count'] as int? ?? 0) > 0) '${t['message_count']} messages',
            ].where((s) => s.isNotEmpty).join('  ·  '),
                style: const TextStyle(fontSize: 12.5, color: kMuted)),
          ]),
        ),
      ),
    );
  }
}

(Color, String) statusStyle(String s) => switch (s) {
      'open' => (kInfo, 'Open'),
      'in_progress' => (kOrange, 'In progress'),
      'waiting_customer' => (kOrange, 'Awaiting you'),
      'resolved' => (kGreen, 'Resolved'),
      'closed' => (kMuted, 'Closed'),
      _ => (kMuted, s),
    };

// ── New request sheet ────────────────────────────────────────────────────────
class _NewTicketSheet extends StatefulWidget {
  const _NewTicketSheet({required this.api});
  final ApiClient api;
  @override
  State<_NewTicketSheet> createState() => _NewTicketSheetState();
}

class _NewTicketSheetState extends State<_NewTicketSheet> {
  final _subject = TextEditingController();
  final _message = TextEditingController();
  String _category = 'mobile';
  bool _busy = false;
  String? _error;

  static const _categories = [
    ('mobile', 'Mobile app'), ('account', 'Account & login'),
    ('jobs', 'Jobs'), ('tasks', 'Tasks'), ('technical', 'Technical problem'),
    ('other', 'Other'),
  ];

  @override
  void dispose() {
    _subject.dispose();
    _message.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_subject.text.trim().isEmpty) {
      setState(() => _error = 'Give your request a short subject.');
      return;
    }
    if (_message.text.trim().isEmpty) {
      setState(() => _error = 'Describe what happened.');
      return;
    }
    setState(() { _busy = true; _error = null; });
    try {
      await widget.api.post('/support-tickets/', {
        'subject': _subject.text.trim(),
        'category': _category,
        'description': _message.text.trim(),
      });
      if (mounted) Navigator.pop(context, true);
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Could not reach the server.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 4, 20, 20),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Align(
            alignment: Alignment.centerLeft,
            child: Text('New support request',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: kInk)),
          ),
          const SizedBox(height: 14),
          LulaTextField(controller: _subject, label: 'Subject', required: true),
          const SizedBox(height: 14),
          LulaDropdown<String>(
            label: 'Category',
            value: _category,
            items: [
              for (final c in _categories)
                DropdownMenuItem(value: c.$1, child: Text(c.$2)),
            ],
            onChanged: (v) => setState(() => _category = v ?? 'mobile'),
          ),
          const SizedBox(height: 14),
          LulaTextField(controller: _message, label: 'What happened?', maxLines: 4, required: true),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(_error!, style: const TextStyle(color: kRed, fontSize: 13)),
          ],
          const SizedBox(height: 18),
          LulaButton(label: 'Send request', loadingLabel: 'Sending…',
              loading: _busy, onPressed: _submit),
        ]),
      ),
    );
  }
}

// ── Ticket detail (conversation) ─────────────────────────────────────────────
class SupportTicketScreen extends StatefulWidget {
  const SupportTicketScreen({super.key, required this.api, required this.ticket});
  final ApiClient api;
  final Map<String, dynamic> ticket;
  @override
  State<SupportTicketScreen> createState() => _SupportTicketScreenState();
}

class _SupportTicketScreenState extends State<SupportTicketScreen> {
  late Map<String, dynamic> _t = widget.ticket;
  final _reply = TextEditingController();
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _reply.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final r = await widget.api.get('/support-tickets/${_t['id']}/');
      if (r is Map) setState(() => _t = r.cast<String, dynamic>());
    } catch (_) {/* keep current */}
  }

  Future<void> _send() async {
    final body = _reply.text.trim();
    if (body.isEmpty || _busy) return;
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final r = await widget.api.post('/support-tickets/${_t['id']}/reply/', {'body': body});
      _reply.clear();
      if (r is Map) setState(() => _t = r.cast<String, dynamic>());
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(const SnackBar(content: Text('Could not reach the server.')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final msgs = (_t['messages'] as List? ?? const []).cast<Map<String, dynamic>>();
    final (c, label) = statusStyle('${_t['status']}');
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 0,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text('${_t['number']}',
                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
            Text('${_t['subject']}',
                maxLines: 1, overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12, color: kMuted)),
          ],
        ),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: Center(
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                decoration: BoxDecoration(
                    color: c.withOpacity(0.13), borderRadius: BorderRadius.circular(8)),
                child: Text(label,
                    style: TextStyle(color: c, fontSize: 11.5, fontWeight: FontWeight.w700)),
              ),
            ),
          ),
        ],
        scrolledUnderElevation: 1,
      ),
      body: Column(children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(14, 14, 14, 14),
            children: [for (final m in msgs) _bubble(m)],
          ),
        ),
        SafeArea(
          top: false,
          child: Container(
            padding: const EdgeInsets.all(8),
            decoration: const BoxDecoration(
                color: Colors.white, border: Border(top: BorderSide(color: kLine))),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _reply,
                  minLines: 1, maxLines: 4,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _send(),
                  decoration: InputDecoration(
                    hintText: 'Reply…',
                    filled: true, fillColor: kBg,
                    contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(22), borderSide: BorderSide.none),
                  ),
                ),
              ),
              const SizedBox(width: 6),
              Material(
                color: kBrand, shape: const CircleBorder(),
                child: InkWell(
                  customBorder: const CircleBorder(),
                  onTap: _busy ? null : _send,
                  child: Padding(
                    padding: const EdgeInsets.all(11),
                    child: _busy
                        ? const SizedBox(width: 20, height: 20,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : const Icon(Icons.send, color: Colors.white, size: 20),
                  ),
                ),
              ),
            ]),
          ),
        ),
      ]),
    );
  }

  Widget _bubble(Map<String, dynamic> m) {
    final fromSupport = m['from_support'] == true;
    final when = DateTime.tryParse('${m['created_at']}')?.toLocal();
    return Align(
      alignment: fromSupport ? Alignment.centerLeft : Alignment.centerRight,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.78),
        padding: const EdgeInsets.fromLTRB(13, 9, 13, 9),
        decoration: BoxDecoration(
          color: fromSupport ? Colors.white : kBrand,
          borderRadius: BorderRadius.circular(14),
          border: fromSupport ? Border.all(color: kLine) : null,
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (fromSupport)
            Padding(
              padding: const EdgeInsets.only(bottom: 3),
              child: Text('${m['sender_name']?.toString().trim().isNotEmpty == true ? m['sender_name'] : 'Lulaworks support'}',
                  style: const TextStyle(
                      fontSize: 11.5, fontWeight: FontWeight.w700, color: kBrandDark)),
            ),
          Text('${m['body']}',
              style: TextStyle(
                  fontSize: 14.5, color: fromSupport ? kInk : Colors.white, height: 1.3)),
          const SizedBox(height: 3),
          Text(
              when == null ? '' : '${when.day}/${when.month} '
                  '${when.hour.toString().padLeft(2, '0')}:${when.minute.toString().padLeft(2, '0')}',
              style: TextStyle(fontSize: 10.5, color: fromSupport ? kMuted : Colors.white70)),
        ]),
      ),
    );
  }
}

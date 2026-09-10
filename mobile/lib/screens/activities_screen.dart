import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';

/// CRM activities — your to-do list of calls, meetings and follow-ups. Tick one
/// off to complete it. Parity with the web CRM's Activities.
class ActivitiesScreen extends StatefulWidget {
  const ActivitiesScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<ActivitiesScreen> createState() => _ActivitiesScreenState();
}

class _ActivitiesScreenState extends State<ActivitiesScreen> {
  bool _mine = true;
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async {
    final q = _mine ? '?mine=1&status=open' : '?status=open';
    final body = await widget.api.get('/activities/$q');
    return pageResults(body).cast<Map<String, dynamic>>();
  }

  void _reload() => setState(() => _future = _load());
  bool get _canEdit => widget.api.can('crm.manage');

  Future<void> _complete(Map<String, dynamic> a) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('/activities/${a['id']}/complete/', {});
      _reload();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Activities'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(46),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
            child: Row(children: [
              _chip('Mine', true),
              const SizedBox(width: 8),
              _chip('All', false),
            ]),
          ),
        ),
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
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('Could not load activities.',
                    style: TextStyle(color: kMuted))),
              ]);
            }
            final acts = snap.data!;
            if (acts.isEmpty) {
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('Nothing due. 🎉', style: TextStyle(color: kMuted))),
              ]);
            }
            return ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: acts.length,
              separatorBuilder: (_, __) => const SizedBox(height: 10),
              itemBuilder: (_, i) => _card(acts[i]),
            );
          },
        ),
      ),
    );
  }

  Widget _chip(String label, bool value) {
    final on = _mine == value;
    return ChoiceChip(
      label: Text(label),
      selected: on,
      selectedColor: kBrandTint,
      onSelected: (_) { setState(() => _mine = value); _reload(); },
    );
  }

  Widget _card(Map<String, dynamic> a) {
    final due = _dueLabel('${a['due_at'] ?? ''}');
    final overdue = _isOverdue('${a['due_at'] ?? ''}');
    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: kLine),
      ),
      child: Row(children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${a['subject']}',
                style: const TextStyle(fontWeight: FontWeight.w600, color: kInk)),
            const SizedBox(height: 2),
            Row(children: [
              Text('${a['type_display'] ?? ''}',
                  style: const TextStyle(fontSize: 12, color: kMuted)),
              if ('${a['customer_name'] ?? ''}'.isNotEmpty)
                Text('  ·  ${a['customer_name']}',
                    style: const TextStyle(fontSize: 12, color: kMuted)),
            ]),
            if (due.isNotEmpty)
              Text(due,
                  style: TextStyle(fontSize: 12,
                      color: overdue ? const Color(0xFFC0392B) : kMuted,
                      fontWeight: overdue ? FontWeight.w600 : FontWeight.w400)),
          ]),
        ),
        if (_canEdit)
          IconButton(
            tooltip: 'Complete',
            icon: const Icon(Icons.check_circle_outline, color: kGreen),
            onPressed: () => _complete(a),
          ),
      ]),
    );
  }

  String _dueLabel(String iso) {
    final d = DateTime.tryParse(iso);
    if (d == null) return '';
    final local = d.toLocal();
    return 'Due ${local.day}/${local.month}/${local.year}';
  }

  bool _isOverdue(String iso) {
    final d = DateTime.tryParse(iso);
    return d != null && d.toLocal().isBefore(DateTime.now());
  }
}

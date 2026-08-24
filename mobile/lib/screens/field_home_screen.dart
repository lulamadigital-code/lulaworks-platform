import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/brand_logo.dart';
import 'my_tasks_screen.dart';
import 'notifications_screen.dart';
import 'task_hub_screen.dart';

/// The field worker's Home — answers "what am I doing today?" in one glance.
/// Not the owner/admin KPI dashboard: a current-task card with the obvious next
/// action, then today's work. Everything routes into the Task Detail.
class FieldHomeScreen extends StatefulWidget {
  const FieldHomeScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<FieldHomeScreen> createState() => _FieldHomeScreenState();
}

const _doneStatuses = {'completed', 'closed', 'cancelled'};

class _FieldHomeScreenState extends State<FieldHomeScreen> {
  late Future<_FieldHome> _future = _load();

  Future<_FieldHome> _load() async {
    final tasks = pageResults(await widget.api.get('/tasks/?mine=1'));
    int unread = 0;
    try {
      final u = await widget.api.get('/notifications/unread/');
      if (u is Map) unread = u['count'] as int? ?? 0;
    } catch (_) {/* non-fatal */}
    return _FieldHome(tasks: tasks, unread: unread);
  }

  void _reload() => setState(() { _future = _load(); });

  void _openTask(Map<String, dynamic> t) async {
    await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => TaskHubScreen(
            api: widget.api, taskId: '${t['id']}', name: '${t['name']}')));
    _reload();
  }

  String _greeting() {
    final h = DateTime.now().hour;
    return h < 12 ? 'Good morning' : (h < 17 ? 'Good afternoon' : 'Good evening');
  }

  String _todayPrefix() {
    final n = DateTime.now();
    return '${n.year.toString().padLeft(4, '0')}-'
        '${n.month.toString().padLeft(2, '0')}-${n.day.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: RefreshIndicator(
          color: kBrand,
          onRefresh: () async => _reload(),
          child: FutureBuilder<_FieldHome>(
            future: _future,
            builder: (context, snap) {
              if (snap.connectionState == ConnectionState.waiting) {
                return const Center(child: CircularProgressIndicator(color: kBrand));
              }
              if (snap.hasError) {
                return ListView(children: [
                  const SizedBox(height: 140),
                  const Icon(Icons.cloud_off, size: 44, color: kMuted),
                  const SizedBox(height: 12),
                  Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
                ]);
              }
              return _content(context, snap.data!);
            },
          ),
        ),
      ),
    );
  }

  Widget _content(BuildContext context, _FieldHome h) {
    final active = h.tasks.where((t) => !_doneStatuses.contains('${t['status']}')).toList();
    // The current task: an in-progress one first, else the next thing to start.
    active.sort((a, b) {
      int rank(Map t) => '${t['status']}' == 'in_progress' ? 0 : 1;
      final r = rank(a) - rank(b);
      if (r != 0) return r;
      return '${a['due_date'] ?? '9999'}'.compareTo('${b['due_date'] ?? '9999'}');
    });
    final current = active.isNotEmpty ? active.first : null;
    final today = _todayPrefix();
    final todays = active.where((t) => '${t['due_date'] ?? ''}' == today).toList();
    final rest = active.where((t) => t != current).toList();
    final doneToday = h.tasks.where((t) => _doneStatuses.contains('${t['status']}')).length;

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
      children: [
        // Header
        Row(children: [
          const BrandLogo(height: 24),
          const Spacer(),
          _bell(context, h.unread),
        ]),
        const SizedBox(height: 18),
        Text('${_greeting()}, ${widget.api.firstName}'.trim(),
            style: const TextStyle(
                fontSize: 24, fontWeight: FontWeight.w700, color: kInk,
                letterSpacing: -0.4),
            maxLines: 1, overflow: TextOverflow.ellipsis),
        const SizedBox(height: 3),
        Text(active.isEmpty
            ? "You're all caught up for today."
            : "You have ${active.length} task${active.length == 1 ? '' : 's'} on the go"
                "${doneToday > 0 ? ' · $doneToday done' : ''}.",
            style: const TextStyle(fontSize: 13.5, color: kMuted)),
        const SizedBox(height: 22),

        // Current task
        if (current != null) ...[
          _sectionTitle('Current task'),
          const SizedBox(height: 10),
          _currentTaskCard(context, current),
        ] else
          _allCaughtUp(context),

        // Today
        if (todays.isNotEmpty) ...[
          const SizedBox(height: 26),
          _sectionTitle("Today"),
          const SizedBox(height: 10),
          ...todays.map((t) => _taskRow(context, t)),
        ],

        // Everything else assigned
        if (rest.isNotEmpty) ...[
          const SizedBox(height: 26),
          Row(children: [
            Expanded(child: _sectionTitle('My work')),
            TextButton(
                onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => MyTasksScreen(api: widget.api))),
                child: const Text('See all')),
          ]),
          const SizedBox(height: 4),
          ...rest.take(6).map((t) => _taskRow(context, t)),
        ],
      ],
    );
  }

  Widget _bell(BuildContext context, int unread) => InkResponse(
        radius: 24,
        onTap: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => NotificationsScreen(api: widget.api))),
        child: Container(
          width: 42, height: 42,
          decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: kLine)),
          child: Badge(
            isLabelVisible: unread > 0,
            label: Text('$unread'),
            offset: const Offset(-6, 6),
            child: const Icon(Icons.notifications_none, size: 21, color: kInk),
          ),
        ),
      );

  Widget _currentTaskCard(BuildContext context, Map<String, dynamic> t) {
    final status = '${t['status']}';
    final inProgress = status == 'in_progress';
    final (c, label) = _statusStyle(status);
    final meta = [
      if ('${t['client_name'] ?? ''}'.isNotEmpty) '${t['client_name']}',
      if ('${t['site'] ?? ''}'.isNotEmpty) '${t['site']}',
    ].join('  ·  ');
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: () => _openTask(t),
        child: Container(
          decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: kBrand.withOpacity(0.35))),
          padding: const EdgeInsets.all(18),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                    color: c.withOpacity(0.13), borderRadius: BorderRadius.circular(8)),
                child: Text(label,
                    style: TextStyle(color: c, fontSize: 12, fontWeight: FontWeight.w700)),
              ),
            ]),
            const SizedBox(height: 12),
            Text('${t['name']}',
                style: const TextStyle(
                    fontSize: 19, fontWeight: FontWeight.w700, color: kInk, height: 1.2)),
            if (meta.isNotEmpty) ...[
              const SizedBox(height: 6),
              Row(children: [
                const Icon(Icons.place_outlined, size: 15, color: kMuted),
                const SizedBox(width: 4),
                Expanded(
                  child: Text(meta,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 13, color: kMuted)),
                ),
              ]),
            ],
            const SizedBox(height: 16),
            SizedBox(
              height: 48,
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: () => _openTask(t),
                icon: Icon(inProgress ? Icons.assignment_turned_in : Icons.play_arrow),
                label: Text(inProgress ? 'Open & report' : 'Open task',
                    style: const TextStyle(fontSize: 15)),
              ),
            ),
          ]),
        ),
      ),
    );
  }

  Widget _taskRow(BuildContext context, Map<String, dynamic> t) {
    final (c, label) = _statusStyle('${t['status']}');
    final sub = [
      if ('${t['client_name'] ?? ''}'.isNotEmpty) '${t['client_name']}',
      if ('${t['site'] ?? ''}'.isNotEmpty) '${t['site']}',
    ].join('  ·  ');
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(13),
        child: InkWell(
          borderRadius: BorderRadius.circular(13),
          onTap: () => _openTask(t),
          child: Container(
            decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(13),
                border: Border.all(color: kLine)),
            padding: const EdgeInsets.fromLTRB(14, 13, 12, 13),
            child: Row(children: [
              Container(width: 4, height: 38,
                  decoration: BoxDecoration(color: c, borderRadius: BorderRadius.circular(3))),
              const SizedBox(width: 12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('${t['name']}',
                      maxLines: 2, overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          fontSize: 14.5, fontWeight: FontWeight.w500, color: kInk)),
                  if (sub.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(sub,
                        maxLines: 1, overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 12, color: kMuted)),
                  ],
                ]),
              ),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                decoration: BoxDecoration(
                    color: c.withOpacity(0.13), borderRadius: BorderRadius.circular(8)),
                child: Text(label,
                    style: TextStyle(color: c, fontSize: 11.5, fontWeight: FontWeight.w600)),
              ),
            ]),
          ),
        ),
      ),
    );
  }

  Widget _allCaughtUp(BuildContext context) => Container(
        width: double.infinity,
        decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: kLine)),
        padding: const EdgeInsets.symmetric(vertical: 34),
        child: Column(children: [
          Container(
            width: 56, height: 56,
            decoration: const BoxDecoration(color: kBrandTint, shape: BoxShape.circle),
            child: const Icon(Icons.check, color: kBrandDark, size: 30),
          ),
          const SizedBox(height: 12),
          const Text('No active tasks',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: kInk)),
          const SizedBox(height: 3),
          const Text('When your manager assigns work, it shows up here.',
              style: TextStyle(fontSize: 13, color: kMuted)),
        ]),
      );

  Widget _sectionTitle(String t) => Text(t.toUpperCase(),
      style: const TextStyle(
          fontSize: 11.5, fontWeight: FontWeight.w700,
          letterSpacing: 0.6, color: kMuted));

  (Color, String) _statusStyle(String s) => switch (s) {
        'in_progress' => (kInfo, 'In progress'),
        'blocked' => (kRed, 'Blocked'),
        'ready' || 'assigned' || 'accepted' => (kOrange, 'Ready to start'),
        _ => (kOrange, 'To do'),
      };
}

class _FieldHome {
  _FieldHome({required this.tasks, required this.unread});
  final List<Map<String, dynamic>> tasks;
  final int unread;
}

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';
import 'task_hub_screen.dart';

/// "My tasks" — the field worker's home base. Only the tasks assigned to the
/// signed-in user (?mine=1), grouped so what's active and what's blocked read at
/// a glance. Tap through to the task hub to capture reports and act.
class MyTasksScreen extends StatefulWidget {
  const MyTasksScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<MyTasksScreen> createState() => _MyTasksScreenState();
}

class _MyTasksScreenState extends State<MyTasksScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('/tasks/?mine=1'));

  static const _order = ['in_progress', 'blocked', 'todo', 'done'];
  static const _done = {'completed', 'closed', 'cancelled'};

  String _bucket(String s) {
    if (s == 'in_progress' || s == 'paused') return 'in_progress';
    if (s == 'blocked') return 'blocked';
    if (_done.contains(s)) return 'done';
    return 'todo';
  }

  static const _label = {
    'in_progress': 'In progress',
    'blocked': 'Blocked',
    'todo': 'To do',
    'done': 'Done',
  };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('My tasks'), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => setState(() { _future = _load(); }),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const _TasksSkeleton();
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 120),
                const Icon(Icons.cloud_off, size: 44, color: kMuted),
                const SizedBox(height: 12),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
              ]);
            }
            final tasks = snap.data ?? const [];
            if (tasks.isEmpty) {
              return ListView(children: [
                const SizedBox(height: 130),
                Container(
                  width: 60, height: 60,
                  margin: const EdgeInsets.symmetric(horizontal: 160),
                  decoration: const BoxDecoration(
                      color: kBrandTint, shape: BoxShape.circle),
                  child: const Icon(Icons.check, color: kBrandDark, size: 30),
                ),
                const SizedBox(height: 14),
                const Center(
                    child: Text('No tasks assigned to you',
                        style: TextStyle(
                            fontSize: 15.5, fontWeight: FontWeight.w600, color: kInk))),
                const SizedBox(height: 2),
                const Center(
                    child: Text("You're all caught up.",
                        style: TextStyle(fontSize: 13, color: kMuted))),
              ]);
            }
            final groups = <String, List<Map<String, dynamic>>>{};
            for (final t in tasks) {
              groups.putIfAbsent(_bucket('${t['status']}'), () => []).add(t);
            }
            return ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 28),
              children: [
                for (final key in _order)
                  if (groups[key] != null) ...[
                    Padding(
                      padding: const EdgeInsets.fromLTRB(4, 16, 4, 8),
                      child: Text('${_label[key]!.toUpperCase()}  ·  ${groups[key]!.length}',
                          style: const TextStyle(
                              fontSize: 11.5, fontWeight: FontWeight.w700,
                              letterSpacing: 0.6, color: kMuted)),
                    ),
                    ...groups[key]!.map((t) => _TaskCard(
                        api: widget.api, task: t,
                        onReturn: () => setState(() { _future = _load(); }))),
                  ],
              ],
            );
          },
        ),
      ),
    );
  }
}

class _TaskCard extends StatelessWidget {
  const _TaskCard(
      {required this.api, required this.task, required this.onReturn});
  final ApiClient api;
  final Map<String, dynamic> task;
  final VoidCallback onReturn;

  (Color, String) _status(String s) => switch (s) {
        'in_progress' => (kInfo, 'In progress'),
        'paused' => (kOrange, 'Paused'),
        'blocked' => (kRed, 'Blocked'),
        'completed' || 'closed' => (kGreen, 'Done'),
        _ => (kOrange, 'To do'),
      };

  @override
  Widget build(BuildContext context) {
    final (c, label) = _status('${task['status']}');
    final dueTxt = dueInfo('${task['due_date'] ?? ''}').$1;
    final progress = task['progress_pct'];
    final sub = [
      if ('${task['site'] ?? ''}'.isNotEmpty) '${task['site']}',
      if (dueTxt.isNotEmpty) dueTxt,
      if (progress != null && '$progress' != '0') '$progress%',
    ].join('  ·  ');
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(13),
        child: InkWell(
          borderRadius: BorderRadius.circular(13),
          onTap: () async {
            await Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => TaskHubScreen(
                    api: api, taskId: '${task['id']}', name: '${task['name']}')));
            onReturn();
          },
          child: ClipRRect(
            borderRadius: BorderRadius.circular(13),
            child: Container(
              decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(13),
                  border: Border.all(color: kLine)),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(14, 13, 12, 13),
                  child: Row(children: [
                    Container(width: 4, height: 38,
                        decoration: BoxDecoration(
                            color: c, borderRadius: BorderRadius.circular(3))),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text('${task['name']}',
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                                fontSize: 14.5, fontWeight: FontWeight.w500, color: kInk)),
                        if (sub.isNotEmpty) ...[
                          const SizedBox(height: 2),
                          Text(sub,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontSize: 12, color: kMuted)),
                        ],
                      ]),
                    ),
                    const SizedBox(width: 8),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                      decoration: BoxDecoration(
                          color: c.withOpacity(0.13),
                          borderRadius: BorderRadius.circular(8)),
                      child: Text(label,
                          style: TextStyle(
                              color: c, fontSize: 11.5, fontWeight: FontWeight.w600)),
                    ),
                  ]),
                ),
                TaskProgressEdge((progress as num?) ?? 0),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

class _TasksSkeleton extends StatelessWidget {
  const _TasksSkeleton();
  @override
  Widget build(BuildContext context) {
    Widget card() => Container(
          margin: const EdgeInsets.only(bottom: 9),
          height: 66,
          decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(13),
              border: Border.all(color: kLine)),
        );
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 28),
      children: [
        Container(width: 90, height: 12,
            decoration: BoxDecoration(color: kLine, borderRadius: BorderRadius.circular(6))),
        const SizedBox(height: 12),
        ...List.generate(5, (_) => card()),
      ],
    );
  }
}

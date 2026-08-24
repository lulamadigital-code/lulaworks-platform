import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../widgets/status_pill.dart';
import 'task_hub_screen.dart';

/// "My tasks" — the field worker's home base. Only the tasks assigned to the
/// signed-in user (?mine=1), grouped so what's active and what's blocked read at
/// a glance. Tap through to the task hub to capture reports, check in, and act.
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

  // Buckets, in the order a worker cares about.
  static const _order = ['in_progress', 'blocked', 'todo', 'done'];
  static const _todo = {'draft', 'ready', 'assigned', 'accepted', 'waiting'};
  static const _done = {'completed', 'closed', 'cancelled'};

  String _bucket(String status) {
    if (status == 'in_progress') return 'in_progress';
    if (status == 'blocked') return 'blocked';
    if (_done.contains(status)) return 'done';
    return _todo.contains(status) ? 'todo' : 'todo';
  }

  static const _bucketLabel = {
    'in_progress': 'In progress',
    'blocked': 'Blocked',
    'todo': 'To do',
    'done': 'Done',
  };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('My tasks')),
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
            final tasks = snap.data ?? const [];
            if (tasks.isEmpty) {
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('No tasks assigned to you.')),
              ]);
            }
            final groups = <String, List<Map<String, dynamic>>>{};
            for (final t in tasks) {
              groups.putIfAbsent(_bucket('${t['status']}'), () => []).add(t);
            }
            return ListView(
              padding: const EdgeInsets.only(bottom: 24),
              children: [
                for (final key in _order)
                  if (groups[key] != null) ...[
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 18, 16, 6),
                      child: Text('${_bucketLabel[key]} (${groups[key]!.length})',
                          style: Theme.of(context).textTheme.titleSmall?.copyWith(
                              color: Theme.of(context).colorScheme.outline)),
                    ),
                    ...groups[key]!.map((t) => _TaskTile(api: widget.api, task: t,
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

class _TaskTile extends StatelessWidget {
  const _TaskTile({required this.api, required this.task, required this.onReturn});
  final ApiClient api;
  final Map<String, dynamic> task;
  final VoidCallback onReturn;

  @override
  Widget build(BuildContext context) {
    final due = '${task['due_date'] ?? ''}';
    final progress = task['progress_pct'];
    return Card(
      margin: const EdgeInsets.fromLTRB(12, 4, 12, 4),
      child: ListTile(
        title: Text('${task['name']}', maxLines: 2, overflow: TextOverflow.ellipsis),
        subtitle: Text([
          if ('${task['site'] ?? ''}'.isNotEmpty) '${task['site']}',
          if (due.isNotEmpty) 'Due $due',
          if (progress != null && '$progress' != '0') '$progress%',
        ].join(' · ')),
        trailing: StatusPill(status: '${task['status']}'),
        onTap: () async {
          await Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => TaskHubScreen(
                api: api, taskId: '${task['id']}', name: '${task['name']}'),
          ));
          onReturn();
        },
      ),
    );
  }
}

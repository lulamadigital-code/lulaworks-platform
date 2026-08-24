import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import 'task_hub_screen.dart';

class ProjectDetailScreen extends StatefulWidget {
  const ProjectDetailScreen({super.key, required this.api, required this.project});
  final ApiClient api;
  final Project project;

  @override
  State<ProjectDetailScreen> createState() => _ProjectDetailScreenState();
}

class _ProjectDetailScreenState extends State<ProjectDetailScreen> {
  late Future<_Detail> _future = _load();

  void _reload() => setState(() { _future = _load(); });

  Future<_Detail> _load() async {
    final readiness = Readiness.fromJson(
        await widget.api.get('/projects/${widget.project.id}/readiness/')
            as Map<String, dynamic>);
    final checklist =
        pageResults(await widget.api.get('/compliance-items/?project=${widget.project.id}'));
    final tasks =
        pageResults(await widget.api.get('/tasks/?project=${widget.project.id}'));
    return _Detail(readiness, checklist, tasks);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.project.number)),
      body: RefreshIndicator(
        onRefresh: () async => setState(() { _future = _load(); }),
        child: FutureBuilder<_Detail>(
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
            final d = snap.data!;
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _header(context),
                const SizedBox(height: 16),
                _GateCard(readiness: d.readiness),
                const SizedBox(height: 16),
                Text('Tasks (${d.tasks.length})',
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 8),
                if (d.tasks.isEmpty)
                  const Text('No tasks yet.'),
                ...d.tasks.map((t) => _TaskTile(
                      task: t,
                      onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => TaskHubScreen(
                            api: widget.api,
                            taskId: '${t['id']}',
                            name: '${t['name']}',
                          ),
                        ),
                      ),
                    )),
                const SizedBox(height: 16),
                Text('Compliance checklist',
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 8),
                ...d.checklist.map((c) => _ChecklistTile(
                      api: widget.api,
                      item: c,
                      onChanged: _reload,
                    )),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _header(BuildContext context) {
    final p = widget.project;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(p.title.isEmpty ? p.clientName : p.title,
          style: Theme.of(context).textTheme.titleLarge),
      const SizedBox(height: 4),
      Text('${p.clientName}${p.site.isNotEmpty ? ' · ${p.site}' : ''}'
          '${p.workType.isNotEmpty ? ' · ${p.workType}' : ''}',
          style: TextStyle(color: Theme.of(context).colorScheme.outline)),
    ]);
  }
}

class _Detail {
  _Detail(this.readiness, this.checklist, this.tasks);
  final Readiness readiness;
  final List<Map<String, dynamic>> checklist;
  final List<Map<String, dynamic>> tasks;
}

class _TaskTile extends StatelessWidget {
  const _TaskTile({required this.task, required this.onTap});
  final Map<String, dynamic> task;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        title: Text('${task['name']}'),
        subtitle: Text('${task['status'] ?? ''}'),
        trailing: const Icon(Icons.chevron_right),
        onTap: onTap,
      ),
    );
  }
}

/// The Work Readiness gate — the hard execution gate, front and centre.
class _GateCard extends StatelessWidget {
  const _GateCard({required this.readiness});
  final Readiness readiness;

  @override
  Widget build(BuildContext context) {
    final open = readiness.open;
    final overridden = readiness.gateStatus == 'overridden';
    final color = open
        ? (overridden ? Colors.amber.shade700 : Colors.green)
        : Colors.red.shade600;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(open ? Icons.verified_user : Icons.gpp_bad, color: color),
            const SizedBox(width: 8),
            Text(
              open
                  ? (overridden ? 'Ready (overridden)' : 'Ready for site')
                  : 'Not ready for site',
              style: Theme.of(context)
                  .textTheme
                  .titleMedium
                  ?.copyWith(color: color, fontWeight: FontWeight.bold),
            ),
            const Spacer(),
            Text('${readiness.overall}%',
                style: Theme.of(context).textTheme.titleLarge),
          ]),
          const SizedBox(height: 12),
          LinearProgressIndicator(
            value: readiness.overall / 100,
            color: color,
            backgroundColor: Theme.of(context).colorScheme.surfaceVariant,
            minHeight: 8,
          ),
          const SizedBox(height: 16),
          ...readiness.categories.entries.map((e) => Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(children: [
                  SizedBox(width: 120, child: Text(_cap(e.key))),
                  Expanded(
                    child: LinearProgressIndicator(
                      value: ((e.value as num).toDouble()) / 100,
                      minHeight: 6,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text('${e.value}%'),
                ]),
              )),
          if (readiness.blocking.isNotEmpty) ...[
            const Divider(height: 24),
            Text('Blocking (${readiness.blocking.length})',
                style: TextStyle(color: color, fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            ...readiness.blocking.map((b) => Text(
                '• ${b['name']}  (${b['source']})',
                style: Theme.of(context).textTheme.bodySmall)),
          ],
        ]),
      ),
    );
  }

  String _cap(String s) => s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);
}

/// An interactive compliance item. Compliance isn't a simple on/off toggle —
/// it's a gated lifecycle (missing → submitted → approved). Tapping opens the
/// valid next actions for the item's current status; the backend enforces the
/// permissions (submit = compliance.manage, approve/reject = compliance.override),
/// and we surface a friendly message if the user isn't allowed.
class _ChecklistTile extends StatelessWidget {
  const _ChecklistTile({
    required this.api,
    required this.item,
    required this.onChanged,
  });

  final ApiClient api;
  final Map<String, dynamic> item;
  final VoidCallback onChanged;

  String get _status => '${item['status']}';
  String get _id => '${item['id']}';

  @override
  Widget build(BuildContext context) {
    final mandatory = item['is_mandatory'] == true;
    final (IconData icon, Color color) = _visual(context);
    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      leading: Icon(icon, color: color, size: 22),
      title: Text('${item['name']}'),
      subtitle: Text('${item['category']} · ${item['status']}'
          '${mandatory ? ' · mandatory' : ''}'),
      trailing: const Icon(Icons.more_horiz),
      onTap: () => _openActions(context),
    );
  }

  (IconData, Color) _visual(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    switch (_status) {
      case 'approved':
        return (
          item['is_satisfied'] == true ? Icons.check_circle : Icons.error,
          item['is_satisfied'] == true ? Colors.green : Colors.amber.shade700,
        );
      case 'submitted':
        return (Icons.hourglass_top, Colors.amber.shade700);
      case 'rejected':
        return (Icons.cancel, Colors.red.shade600);
      case 'expired':
        return (Icons.event_busy, Colors.red.shade600);
      default: // missing, pending
        return (Icons.radio_button_unchecked, scheme.outline);
    }
  }

  void _openActions(BuildContext context) {
    // Offer only the transitions that make sense from the current status AND
    // that this user is permitted to make (submit = compliance.manage,
    // approve/reject = compliance.override). The backend enforces the same.
    final canSubmit = api.canManageCompliance;
    final canDecide = api.canOverrideCompliance;
    final actions = <_Act>[
      if (canSubmit && _status != 'submitted' && _status != 'approved')
        const _Act('Mark as submitted', Icons.upload_file, 'submit'),
      if (canDecide && _status == 'submitted')
        const _Act('Approve', Icons.verified, 'approve'),
      if (canDecide && (_status == 'submitted' || _status == 'approved'))
        const _Act('Reject', Icons.block, 'reject', destructive: true),
    ];
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (sheetCtx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 4, 20, 4),
              child: Text('${item['name']}',
                  style: Theme.of(sheetCtx).textTheme.titleMedium),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
              child: Text('Currently: ${item['status']}',
                  style: Theme.of(sheetCtx).textTheme.bodySmall?.copyWith(
                      color: Theme.of(sheetCtx).colorScheme.outline)),
            ),
            const Divider(height: 1),
            for (final a in actions)
              ListTile(
                leading: Icon(a.icon,
                    color: a.destructive
                        ? Theme.of(sheetCtx).colorScheme.error
                        : null),
                title: Text(a.label,
                    style: a.destructive
                        ? TextStyle(color: Theme.of(sheetCtx).colorScheme.error)
                        : null),
                onTap: () {
                  Navigator.pop(sheetCtx);
                  _run(context, a);
                },
              ),
            if (actions.isEmpty)
              const Padding(
                padding: EdgeInsets.all(20),
                child: Text('No actions available to you for this item.'),
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  Future<void> _run(BuildContext context, _Act a) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await api.post('/compliance-items/$_id/${a.endpoint}/');
      onChanged();
      messenger.showSnackBar(SnackBar(content: Text('${a.label} — done')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
        content: Text(e.isForbidden
            ? "You don't have permission to ${a.label.toLowerCase()}."
            : e.message),
      ));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }
}

class _Act {
  const _Act(this.label, this.icon, this.endpoint, {this.destructive = false});
  final String label;
  final IconData icon;
  final String endpoint;
  final bool destructive;
}

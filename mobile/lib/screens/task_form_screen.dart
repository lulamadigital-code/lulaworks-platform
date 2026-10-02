import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../ui/tokens.dart';
import '../widgets/lula_ui.dart';

/// Create a task — the mobile New-Work form, matching the web wizard:
///   • a job is OPTIONAL (standalone work is allowed),
///   • people are assigned BY ROLE — Owner (accountable), Execution team,
///     Watchers (notified, read-only), Approvers (sign-off).
/// Posts to /tasks/ (work.create), which routes through create_work on the
/// backend — same logic as the web. When opened from a job, the job is fixed.
class TaskFormScreen extends StatefulWidget {
  const TaskFormScreen(
      {super.key, required this.api, this.projectId, this.projectName});
  final ApiClient api;
  final String? projectId;
  final String? projectName;

  @override
  State<TaskFormScreen> createState() => _TaskFormScreenState();
}

class _TaskFormScreenState extends State<TaskFormScreen> {
  final _name = TextEditingController();
  final _desc = TextEditingController();
  String? _projectId;
  DateTime? _due;
  String _priority = 'medium';

  // Assignment by role (user ids). Owner defaults to the creator ('' = me).
  String _ownerId = '';
  final Set<String> _executors = {};
  final Set<String> _watchers = {};
  final Set<String> _approvers = {};

  List<Map<String, dynamic>> _projects = const [];
  List<Map<String, dynamic>> _people = const []; // {id, name, role}
  bool _loading = true;
  bool _saving = false;
  String? _error;

  static const _priorities = [
    ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical'),
  ];

  @override
  void initState() {
    super.initState();
    _projectId = widget.projectId;
    _load();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([
        widget.projectId == null
            ? widget.api.get('/projects/')
            : Future<dynamic>.value(null),
        widget.api.get('/users/').catchError((_) => null),
      ]);
      final projects =
          results[0] == null ? <Map<String, dynamic>>[] : pageResults(results[0]);
      final people = <Map<String, dynamic>>[];
      for (final row in pageResults(results[1])) {
        final u = (row['user'] as Map?)?.cast<String, dynamic>() ?? const {};
        final id = '${u['id'] ?? ''}';
        if (id.isEmpty) continue;
        final name = '${u['full_name'] ?? ''}'.trim().isNotEmpty
            ? '${u['full_name']}'
            : '${u['first_name'] ?? ''} ${u['last_name'] ?? ''}'.trim();
        people.add({
          'id': id,
          'name': name.isEmpty ? '${u['email'] ?? 'User'}' : name,
          'role': '${row['role_name'] ?? row['job_title'] ?? ''}',
        });
      }
      if (mounted) {
        setState(() {
          _projects = projects;
          _people = people;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  void dispose() {
    _name.dispose();
    _desc.dispose();
    super.dispose();
  }

  Future<void> _pickDue() async {
    final now = DateTime.now();
    final d = await showDatePicker(
        context: context,
        initialDate: _due ?? now,
        firstDate: now.subtract(const Duration(days: 1)),
        lastDate: now.add(const Duration(days: 3650)));
    if (d != null) setState(() => _due = d);
  }

  String _nameOf(String id) =>
      _people.firstWhere((p) => p['id'] == id, orElse: () => const {})['name']?.toString() ??
      'User';

  Future<void> _pickPeople(String title, Set<String> target) async {
    final temp = Set<String>.from(target);
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) => SafeArea(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
              child: Align(alignment: Alignment.centerLeft,
                  child: Text(title, style: LwType.title)),
            ),
            Flexible(
              child: ListView(shrinkWrap: true, children: [
                for (final p in _people)
                  CheckboxListTile(
                    value: temp.contains(p['id']),
                    onChanged: (v) => setSheet(() =>
                        v == true ? temp.add('${p['id']}') : temp.remove(p['id'])),
                    title: Text('${p['name']}'),
                    subtitle: '${p['role'] ?? ''}'.isEmpty ? null : Text('${p['role']}'),
                  ),
              ]),
            ),
            Padding(
              padding: const EdgeInsets.all(16),
              child: SizedBox(
                width: double.infinity,
                child: FilledButton(
                    onPressed: () => Navigator.pop(ctx), child: const Text('Done')),
              ),
            ),
          ]),
        ),
      ),
    );
    setState(() {
      target..clear()..addAll(temp);
    });
  }

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    if (_name.text.trim().isEmpty) {
      setState(() => _error = 'A task name is required.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final body = <String, dynamic>{
      'name': _name.text.trim(),
      'description': _desc.text.trim(),
      'priority': _priority,
      if (_projectId != null && _projectId!.isNotEmpty) 'project': _projectId,
      if (_due != null)
        'due_date':
            '${_due!.year.toString().padLeft(4, '0')}-${_due!.month.toString().padLeft(2, '0')}-${_due!.day.toString().padLeft(2, '0')}',
      if (_ownerId.isNotEmpty) 'owner': _ownerId, // else backend assigns the creator
      if (_executors.isNotEmpty) 'executors': _executors.toList(),
      if (_watchers.isNotEmpty) 'watchers': _watchers.toList(),
      if (_approvers.isNotEmpty) 'approvers': _approvers.toList(),
    };
    final messenger = ScaffoldMessenger.of(context);
    try {
      final saved = await widget.api.post('/tasks/', body);
      if (!mounted) return;
      messenger.showSnackBar(const SnackBar(content: Text('Task created')));
      Navigator.of(context).pop(saved);
    } on ApiException catch (e) {
      setState(() => _error = e.isForbidden
          ? "You don't have permission to create tasks."
          : e.message);
    } catch (_) {
      setState(() => _error = 'Could not reach the server.');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('New task'), scrolledUnderElevation: 1),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(LwSpace.xl),
              children: [
                // Job — fixed when opened from a job, otherwise OPTIONAL.
                if (widget.projectId != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: LwSpace.lg),
                    child: Text('Job: ${widget.projectName ?? ''}', style: LwType.label),
                  )
                else ...[
                  LulaDropdown<String>(
                    label: 'Job (optional)',
                    value: _projectId,
                    items: [
                      const DropdownMenuItem(
                          value: null, child: Text('No job — standalone task')),
                      for (final p in _projects)
                        DropdownMenuItem(
                            value: '${p['id']}',
                            child: Text(
                                '${p['number'] ?? ''} · ${p['client_name'] ?? p['title'] ?? ''}',
                                overflow: TextOverflow.ellipsis)),
                    ],
                    onChanged: (v) => setState(() => _projectId = v),
                  ),
                  const SizedBox(height: LwSpace.lg),
                ],
                LulaTextField(controller: _name, label: 'Task name', required: true),
                const SizedBox(height: LwSpace.lg),
                LulaTextField(controller: _desc, label: 'Description', maxLines: 3),
                const SizedBox(height: LwSpace.lg),
                LulaDropdown<String>(
                  label: 'Priority',
                  value: _priority,
                  items: [
                    for (final p in _priorities)
                      DropdownMenuItem(value: p.$1, child: Text(p.$2)),
                  ],
                  onChanged: (v) => setState(() => _priority = v ?? 'medium'),
                ),
                const SizedBox(height: LwSpace.lg),
                InkWell(
                  onTap: _pickDue,
                  borderRadius: BorderRadius.circular(12),
                  child: InputDecorator(
                    decoration: InputDecoration(
                      labelText: 'Due date (optional)',
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                    ),
                    child: Text(
                      _due == null
                          ? 'Not set'
                          : '${_due!.year}-${_due!.month.toString().padLeft(2, '0')}-${_due!.day.toString().padLeft(2, '0')}',
                      style: TextStyle(color: _due == null ? kMuted : kInk),
                    ),
                  ),
                ),

                const SizedBox(height: LwSpace.xxl),
                Text('TEAM', style: LwType.section),
                const SizedBox(height: LwSpace.sm),
                // Owner — the single accountable person (defaults to you).
                LulaDropdown<String>(
                  label: 'Owner',
                  value: _ownerId,
                  items: [
                    const DropdownMenuItem(value: '', child: Text('Me (you)')),
                    for (final p in _people)
                      if ('${p['id']}' != widget.api.userId)
                        DropdownMenuItem(
                            value: '${p['id']}',
                            child: Text('${p['name']}', overflow: TextOverflow.ellipsis)),
                  ],
                  onChanged: (v) => setState(() => _ownerId = v ?? ''),
                ),
                const SizedBox(height: LwSpace.md),
                _peopleRow('Execution team', _executors,
                    'Who does the work', Icons.engineering_outlined),
                _peopleRow('Watchers', _watchers,
                    'Notified, read-only', Icons.visibility_outlined),
                _peopleRow('Approvers', _approvers,
                    'Sign off completion', Icons.verified_outlined),

                if (_error != null) ...[
                  const SizedBox(height: LwSpace.lg),
                  Text(_error!, style: TextStyle(color: kRed, fontSize: 13)),
                ],
                const SizedBox(height: LwSpace.xl),
                LulaButton(
                    label: 'Create task',
                    loadingLabel: 'Saving…',
                    loading: _saving,
                    onPressed: _save),
                const SizedBox(height: LwSpace.xxl),
              ],
            ),
    );
  }

  Widget _peopleRow(String label, Set<String> selected, String hint, IconData icon) {
    return Padding(
      padding: const EdgeInsets.only(bottom: LwSpace.sm),
      child: InkWell(
        borderRadius: LwRadius.card,
        onTap: () => _pickPeople(label, selected),
        child: Container(
          padding: const EdgeInsets.all(LwSpace.md),
          decoration: BoxDecoration(border: Border.all(color: kLine), borderRadius: LwRadius.card),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Icon(icon, size: 18, color: kMuted),
              const SizedBox(width: LwSpace.sm),
              Text(label, style: LwType.label),
              const Spacer(),
              Text(selected.isEmpty ? hint : '${selected.length} selected',
                  style: LwType.caption),
              Icon(Icons.chevron_right, size: 18, color: kMuted),
            ]),
            if (selected.isNotEmpty) ...[
              const SizedBox(height: LwSpace.sm),
              Wrap(spacing: 6, runSpacing: 6, children: [
                for (final id in selected)
                  Chip(
                    label: Text(_nameOf(id), style: const TextStyle(fontSize: 12)),
                    visualDensity: VisualDensity.compact,
                    onDeleted: () => setState(() => selected.remove(id)),
                  ),
              ]),
            ],
          ]),
        ),
      ),
    );
  }
}

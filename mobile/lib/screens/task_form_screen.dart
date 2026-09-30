import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

/// Create a task on a job. Posts to /tasks/ (execution.manage). When opened from
/// a job, the project is fixed; otherwise the user picks one.
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
  List<Map<String, dynamic>> _projects = const [];
  bool _loading = true;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _projectId = widget.projectId;
    if (widget.projectId != null) {
      _loading = false;
    } else {
      _loadProjects();
    }
  }

  Future<void> _loadProjects() async {
    try {
      final rows = pageResults(await widget.api.get('/projects/'));
      if (!mounted) return;
      setState(() {
        _projects = rows;
        _loading = false;
      });
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

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    if (_projectId == null || _projectId!.isEmpty) {
      setState(() => _error = 'Choose the job this task belongs to.');
      return;
    }
    if (_name.text.trim().isEmpty) {
      setState(() => _error = 'A task name is required.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final body = <String, dynamic>{
      'project': _projectId,
      'name': _name.text.trim(),
      'description': _desc.text.trim(),
      if (_due != null)
        'due_date':
            '${_due!.year.toString().padLeft(4, '0')}-${_due!.month.toString().padLeft(2, '0')}-${_due!.day.toString().padLeft(2, '0')}',
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
              padding: const EdgeInsets.all(20),
              children: [
                if (widget.projectId != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 16),
                    child: Text('Job: ${widget.projectName ?? ''}',
                        style: const TextStyle(
                            fontWeight: FontWeight.w600, color: kInk)),
                  )
                else ...[
                  LulaDropdown<String>(
                    label: 'Job',
                    required: true,
                    value: _projectId,
                    items: [
                      const DropdownMenuItem(value: null, child: Text('Choose a job…')),
                      for (final p in _projects)
                        DropdownMenuItem(
                            value: '${p['id']}',
                            child: Text(
                                '${p['number'] ?? ''} · ${p['client_name'] ?? p['title'] ?? ''}',
                                overflow: TextOverflow.ellipsis)),
                    ],
                    onChanged: (v) => setState(() => _projectId = v),
                  ),
                  const SizedBox(height: 16),
                ],
                LulaTextField(
                    controller: _name, label: 'Task name', required: true),
                const SizedBox(height: 16),
                LulaTextField(
                    controller: _desc, label: 'Description', maxLines: 3),
                const SizedBox(height: 16),
                InkWell(
                  onTap: _pickDue,
                  borderRadius: BorderRadius.circular(12),
                  child: InputDecorator(
                    decoration: InputDecoration(
                      labelText: 'Due date (optional)',
                      border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12)),
                    ),
                    child: Text(
                      _due == null
                          ? 'Not set'
                          : '${_due!.year}-${_due!.month.toString().padLeft(2, '0')}-${_due!.day.toString().padLeft(2, '0')}',
                      style: TextStyle(color: _due == null ? kMuted : kInk),
                    ),
                  ),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 16),
                  Text(_error!,
                      style: const TextStyle(color: kRed, fontSize: 13)),
                ],
                const SizedBox(height: 22),
                LulaButton(
                    label: 'Create task',
                    loadingLabel: 'Saving…',
                    loading: _saving,
                    onPressed: _save),
                const SizedBox(height: 24),
              ],
            ),
    );
  }
}

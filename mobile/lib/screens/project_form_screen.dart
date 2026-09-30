import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

/// Create a job by AWARDING a quotation (the real workflow: quote → job). Posts
/// to /projects/ with the quotation id (projects.create). Only accepted/approved
/// quotations can be awarded; the backend enforces it and returns a clear error.
class ProjectFormScreen extends StatefulWidget {
  const ProjectFormScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<ProjectFormScreen> createState() => _ProjectFormScreenState();
}

class _ProjectFormScreenState extends State<ProjectFormScreen> {
  final _workType = TextEditingController();
  final _site = TextEditingController();
  String? _quotationId;
  List<Map<String, dynamic>> _quotes = const [];
  bool _loading = true;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadQuotes();
  }

  Future<void> _loadQuotes() async {
    try {
      // Prefer awardable quotations (accepted / approved / awarded); fall back to
      // all if the status filter returns nothing.
      var rows = pageResults(await widget.api.get('/quotations/'));
      const awardable = {'accepted', 'approved', 'awarded', 'sent', 'issued'};
      final ready =
          rows.where((q) => awardable.contains('${q['status']}')).toList();
      if (!mounted) return;
      setState(() {
        _quotes = ready.isNotEmpty ? ready : rows;
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  void dispose() {
    _workType.dispose();
    _site.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    if (_quotationId == null || _quotationId!.isEmpty) {
      setState(() => _error = 'Choose the quotation to turn into a job.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final body = <String, dynamic>{
      'quotation': _quotationId,
      'work_type': _workType.text.trim(),
      'site': _site.text.trim(),
    };
    final messenger = ScaffoldMessenger.of(context);
    try {
      final saved = await widget.api.post('/projects/', body);
      if (!mounted) return;
      messenger.showSnackBar(const SnackBar(content: Text('Job created')));
      Navigator.of(context).pop(saved);
    } on ApiException catch (e) {
      setState(() => _error = e.isForbidden
          ? "You don't have permission to create jobs."
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
      appBar: AppBar(title: const Text('New job'), scrolledUnderElevation: 1),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                const Text('A job is created by awarding a quotation.',
                    style: TextStyle(color: kMuted, fontSize: 13)),
                const SizedBox(height: 16),
                LulaDropdown<String>(
                  label: 'Quotation',
                  required: true,
                  value: _quotationId,
                  items: [
                    const DropdownMenuItem(
                        value: null, child: Text('Choose a quotation…')),
                    for (final q in _quotes)
                      DropdownMenuItem(
                          value: '${q['id']}',
                          child: Text(
                              '${q['number'] ?? ''} · ${q['client_name'] ?? q['title'] ?? ''}',
                              overflow: TextOverflow.ellipsis)),
                  ],
                  onChanged: (v) => setState(() => _quotationId = v),
                ),
                const SizedBox(height: 16),
                LulaTextField(
                    controller: _workType,
                    label: 'Work type (optional)',
                    hint: 'e.g. Hydraulic maintenance'),
                const SizedBox(height: 16),
                LulaTextField(
                    controller: _site, label: 'Site (optional)'),
                if (_error != null) ...[
                  const SizedBox(height: 16),
                  Text(_error!,
                      style: const TextStyle(color: kRed, fontSize: 13)),
                ],
                const SizedBox(height: 22),
                LulaButton(
                    label: 'Create job',
                    loadingLabel: 'Saving…',
                    loading: _saving,
                    onPressed: _save),
                const SizedBox(height: 24),
              ],
            ),
    );
  }
}

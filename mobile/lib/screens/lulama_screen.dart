import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';

class LulamaScreen extends StatefulWidget {
  const LulamaScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<LulamaScreen> createState() => _LulamaScreenState();
}

class _LulamaScreenState extends State<LulamaScreen> {
  final _request = TextEditingController(text: 'Prepare this project');
  List<Project> _projects = const [];
  String? _projectId;
  bool _busy = false;
  String? _error;
  AiDraft? _draft;

  @override
  void initState() {
    super.initState();
    _loadProjects();
  }

  Future<void> _loadProjects() async {
    try {
      final body = await widget.api.get('/projects/');
      setState(() => _projects = pageResults(body).map(Project.fromJson).toList());
    } catch (_) {/* projects are optional context */}
  }

  Future<void> _ask() async {
    setState(() {
      _busy = true;
      _error = null;
      _draft = null;
    });
    try {
      final body = await widget.api.post('/ai/interactions/ask/', {
        'request': _request.text.trim(),
        if (_projectId != null) 'project': _projectId,
      });
      setState(() => _draft = AiDraft.fromJson(body as Map<String, dynamic>));
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Could not reach Lulama.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Row(children: [
        Icon(Icons.auto_awesome), SizedBox(width: 8), Text('Lulama'),
      ])),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('Your AI Operations Director',
              style: Theme.of(context).textTheme.titleMedium),
          Text('Lulama coordinates the specialised agents and returns one draft '
              'for you to review. It proposes — you approve.',
              style: TextStyle(color: Theme.of(context).colorScheme.outline)),
          const SizedBox(height: 16),
          if (_projects.isNotEmpty)
            DropdownButtonFormField<String?>(
              value: _projectId,
              decoration: const InputDecoration(
                  labelText: 'Project (optional)', border: OutlineInputBorder()),
              items: [
                const DropdownMenuItem(value: null, child: Text('Portfolio-wide')),
                ..._projects.map((p) => DropdownMenuItem(
                    value: p.id, child: Text('${p.number} · ${p.clientName}'))),
              ],
              onChanged: (v) => setState(() => _projectId = v),
            ),
          const SizedBox(height: 12),
          TextField(
            controller: _request,
            minLines: 1,
            maxLines: 3,
            decoration: const InputDecoration(
                labelText: 'Ask Lulama', border: OutlineInputBorder()),
          ),
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed: _busy ? null : _ask,
            icon: _busy
                ? const SizedBox(
                    height: 18, width: 18,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.send),
            label: const Text('Ask'),
          ),
          if (_error != null) ...[
            const SizedBox(height: 16),
            Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          if (_draft != null) ...[
            const SizedBox(height: 24),
            _DraftView(draft: _draft!),
          ],
        ],
      ),
    );
  }
}

class _DraftView extends StatelessWidget {
  const _DraftView({required this.draft});
  final AiDraft draft;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Text('Consolidated draft', style: theme.textTheme.titleMedium),
        const Spacer(),
        Chip(label: Text('confidence ${draft.confidence}'), visualDensity: VisualDensity.compact),
      ]),
      if (draft.briefing != null) ...[
        const SizedBox(height: 8),
        Card(
          color: theme.colorScheme.secondaryContainer,
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Text(draft.briefing!),
          ),
        ),
      ],
      const SizedBox(height: 8),
      ...draft.agents.map((a) => Card(
            child: ListTile(
              leading: const Icon(Icons.smart_toy_outlined),
              title: Text(_agentName(a['agent'] as String? ?? '')),
              subtitle: Text('${a['summary']}'),
            ),
          )),
      if (draft.proposedActions.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text('Proposed actions', style: theme.textTheme.titleSmall),
        const SizedBox(height: 4),
        ...draft.proposedActions.map((p) => ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(
                p['requires_approval'] == true ? Icons.pending_actions : Icons.lightbulb_outline,
                color: p['requires_approval'] == true ? Colors.amber.shade800 : null,
              ),
              title: Text('${p['description']}'),
              trailing: p['requires_approval'] == true
                  ? const Chip(
                      label: Text('needs approval'),
                      visualDensity: VisualDensity.compact)
                  : null,
            )),
        Text('Lulama never executes these — a human approves them.',
            style: theme.textTheme.bodySmall
                ?.copyWith(color: theme.colorScheme.outline)),
      ],
      if (draft.omittedAgents.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text('Withheld (your permissions): '
            '${draft.omittedAgents.map((o) => o['agent']).join(', ')}',
            style: theme.textTheme.bodySmall
                ?.copyWith(color: theme.colorScheme.outline)),
      ],
    ]);
  }

  String _agentName(String key) => switch (key) {
        'rfq' => 'RFQ / Document',
        'procurement' => 'Procurement',
        'estimating' => 'Estimating',
        'compliance' => 'Compliance',
        'project' => 'Project Manager',
        'commercial' => 'Commercial',
        'executive' => 'Executive',
        _ => key,
      };
}

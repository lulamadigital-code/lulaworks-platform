import 'package:flutter/material.dart';

import '../api/api_client.dart';

/// LulaAI — the intelligence layer, at parity with the web console.
///
/// A daily briefing ("what needs attention"), grounded answers from the
/// company's real data (never invented), and write actions prepared as an
/// editable draft that only runs on explicit confirmation. Every question and
/// action is permission-checked server-side by the same assistant the web uses.
class LulaAiScreen extends StatefulWidget {
  const LulaAiScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<LulaAiScreen> createState() => _LulaAiScreenState();
}

class _LulaAiScreenState extends State<LulaAiScreen> {
  final _message = TextEditingController();
  bool _busy = false;
  String? _error;
  Map<String, dynamic>? _result; // ask/execute response
  Map<String, dynamic>? _brief;
  Map<String, TextEditingController> _draftCtrls = {};

  @override
  void initState() {
    super.initState();
    _loadBrief();
  }

  @override
  void dispose() {
    _message.dispose();
    for (final c in _draftCtrls.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _loadBrief() async {
    try {
      final body = await widget.api.get('/ai/assistant/brief/');
      if (mounted && body is Map) {
        setState(() => _brief = body.cast<String, dynamic>());
      }
    } catch (_) {/* the briefing is a nicety — never block the screen */}
  }

  Future<void> _ask(String message) async {
    final text = message.trim();
    if (text.isEmpty) return;
    _message.text = text;
    _setBusy();
    try {
      final body =
          await widget.api.post('/ai/assistant/ask/', {'message': text});
      _applyResult(body);
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Could not reach LulaWorks.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _execute(String action) async {
    final params = <String, dynamic>{'action': action};
    _draftCtrls.forEach((k, c) => params[k] = c.text);
    final draft = (_result?['draft'] as Map?)?.cast<String, dynamic>();
    if (draft != null && draft['customer_id'] != null) {
      params['customer_id'] = draft['customer_id'];
    }
    _setBusy();
    try {
      final body = await widget.api.post('/ai/assistant/execute/', params);
      _applyResult(body);
      await _loadBrief(); // a write may change what needs attention
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Could not complete that action.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _setBusy() => setState(() {
        _busy = true;
        _error = null;
      });

  void _applyResult(dynamic body) {
    for (final c in _draftCtrls.values) {
      c.dispose();
    }
    _draftCtrls = {};
    final map = (body is Map) ? body.cast<String, dynamic>() : <String, dynamic>{};
    if (map['kind'] == 'draft') {
      for (final f in (map['fields'] as List? ?? const [])) {
        final name = '${(f as Map)['name']}';
        _draftCtrls[name] = TextEditingController(text: '${f['value'] ?? ''}');
      }
    }
    setState(() => _result = map);
  }

  // Permission-aware suggestions — mirrors the web quick actions; never offers
  // what the user can't do (the backend enforces it regardless).
  List<(String, String)> get _quick {
    final api = widget.api;
    final out = <(String, String)>[];
    if (api.can('projects.view')) {
      out.add(('Overdue work', 'Show me overdue tasks'));
      out.add(('My tasks', 'What are my tasks?'));
      out.add(('Quotations', 'Which quotations are awaiting approval?'));
    }
    if (api.canViewMoney) out.add(('Unpaid invoices', 'Which invoices are unpaid?'));
    if (api.canProcurement) {
      out.add(('Supplier price', 'Supplier price for hydraulic pipe'));
    }
    if (api.canManageCustomers) {
      out.add(('Follow-ups', "Which customers haven't been contacted in 30 days?"));
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Row(children: [
          Icon(Icons.auto_awesome), SizedBox(width: 8), Text('LulaAI'),
        ]),
      ),
      body: RefreshIndicator(
        onRefresh: _loadBrief,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_result == null && _brief != null) _briefCard(context),
            _askBox(context),
            const SizedBox(height: 12),
            if (_result == null) _quickChips(context),
            if (_error != null) ...[
              const SizedBox(height: 16),
              _errorCard(context, _error!),
            ],
            if (_result != null) ...[
              const SizedBox(height: 16),
              _resultView(context),
            ],
          ],
        ),
      ),
    );
  }

  // ── Daily briefing ──────────────────────────────────────────────────────────
  Widget _briefCard(BuildContext context) {
    final theme = Theme.of(context);
    final b = _brief!;
    final items = (b['items'] as List? ?? const []);
    return Card(
      margin: const EdgeInsets.only(bottom: 16),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.wb_sunny_outlined, size: 20),
            const SizedBox(width: 8),
            Expanded(
              child: Text('${b['greeting'] ?? ''}',
                  style: theme.textTheme.titleMedium
                      ?.copyWith(fontWeight: FontWeight.w700)),
            ),
          ]),
          const SizedBox(height: 4),
          Text('${b['line'] ?? ''}',
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: theme.colorScheme.outline)),
          if (items.isNotEmpty) ...[
            const SizedBox(height: 12),
            Wrap(spacing: 8, runSpacing: 8, children: [
              for (final i in items)
                ActionChip(
                  label: Text('${i['count']} ${i['label']}'),
                  backgroundColor: _sevColor(context, '${i['severity']}'),
                  onPressed: _busy ? null : () => _ask('${i['query']}'),
                ),
            ]),
          ],
        ]),
      ),
    );
  }

  Color? _sevColor(BuildContext context, String sev) {
    final scheme = Theme.of(context).colorScheme;
    return switch (sev) {
      'bad' => scheme.errorContainer,
      'warn' => Colors.amber.withOpacity(0.22),
      _ => scheme.secondaryContainer,
    };
  }

  // ── Ask box + quick actions ───────────────────────────────────────────────────
  Widget _askBox(BuildContext context) {
    return Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
      Expanded(
        child: TextField(
          controller: _message,
          minLines: 1,
          maxLines: 3,
          textInputAction: TextInputAction.send,
          onSubmitted: _busy ? null : _ask,
          decoration: const InputDecoration(
            labelText: 'Ask about jobs, suppliers, customers…',
            border: OutlineInputBorder(),
          ),
        ),
      ),
      const SizedBox(width: 8),
      FilledButton(
        onPressed: _busy ? null : () => _ask(_message.text),
        child: _busy
            ? const SizedBox(
                height: 18, width: 18,
                child: CircularProgressIndicator(strokeWidth: 2))
            : const Icon(Icons.send),
      ),
    ]);
  }

  Widget _quickChips(BuildContext context) {
    final quick = _quick;
    if (quick.isEmpty) return const SizedBox.shrink();
    return Wrap(spacing: 8, runSpacing: 8, children: [
      for (final (label, q) in quick)
        ActionChip(label: Text(label), onPressed: _busy ? null : () => _ask(q)),
    ]);
  }

  // ── Result rendering ──────────────────────────────────────────────────────────
  Widget _resultView(BuildContext context) {
    final r = _result!;
    return switch (r['kind']) {
      'draft' => _draftCard(context, r),
      'result' => _resultCard(context, r),
      _ => _answerCard(context, r),
    };
  }

  Widget _answerCard(BuildContext context, Map<String, dynamic> r) {
    final theme = Theme.of(context);
    final items = (r['items'] as List? ?? const []);
    final sources = (r['sources'] as List? ?? const []);
    final denied = r['denied'] == true;
    return Card(
      color: denied ? theme.colorScheme.errorContainer : null,
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Icon(Icons.auto_awesome, size: 20),
            const SizedBox(width: 8),
            Expanded(
              child: Text('${r['answer'] ?? ''}',
                  style: theme.textTheme.bodyLarge
                      ?.copyWith(fontWeight: FontWeight.w600)),
            ),
          ]),
          if (r['confidence'] != null && r['confidence'] != 'high') ...[
            const SizedBox(height: 8),
            Chip(
              label: Text('${r['confidence']} confidence'),
              visualDensity: VisualDensity.compact),
          ],
          for (final item in items) _itemTile(context, item),
          if (sources.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('Source: ${sources.join(' · ')}',
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.outline)),
          ],
        ]),
      ),
    );
  }

  Widget _itemTile(BuildContext context, dynamic item) {
    final map = (item as Map).cast<String, dynamic>();
    final title = map['name'] ?? map['number'] ?? map['client'] ??
        map['supplier'] ?? map.values.first;
    final parts = <String>[];
    map.forEach((k, v) {
      if (k == 'id' || k == 'source') return;
      if (v == title) return;
      if (v == null || '$v'.isEmpty) return;
      parts.add('${_titleCase(k)}: $v');
    });
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('$title', style: const TextStyle(fontWeight: FontWeight.w600)),
        if (parts.isNotEmpty)
          Text(parts.join('  ·  '),
              style: Theme.of(context).textTheme.bodySmall),
      ]),
    );
  }

  Widget _draftCard(BuildContext context, Map<String, dynamic> r) {
    final theme = Theme.of(context);
    final action = '${r['action']}';
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Icon(Icons.edit_note, size: 20),
            const SizedBox(width: 8),
            Expanded(
              child: Text('${r['answer'] ?? ''}',
                  style: theme.textTheme.bodyLarge
                      ?.copyWith(fontWeight: FontWeight.w600)),
            ),
          ]),
          if (r['high_risk'] == true) ...[
            const SizedBox(height: 8),
            Chip(
              avatar: const Icon(Icons.lock_outline, size: 16),
              label: const Text('Confirmation required to send'),
              backgroundColor: Colors.amber.withOpacity(0.22),
              visualDensity: VisualDensity.compact),
          ],
          const SizedBox(height: 8),
          for (final f in (r['fields'] as List? ?? const []))
            _draftField(f as Map),
          const SizedBox(height: 14),
          Row(children: [
            FilledButton.icon(
              onPressed: _busy ? null : () => _execute(action),
              icon: const Icon(Icons.check),
              label: Text(_confirmLabel(action)),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Text('LulaAI never sends without this confirmation.',
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.outline)),
            ),
          ]),
        ]),
      ),
    );
  }

  Widget _draftField(Map f) {
    final name = '${f['name']}';
    final ctrl = _draftCtrls[name];
    final isBody = f['type'] == 'textarea';
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: TextField(
        controller: ctrl,
        minLines: isBody ? 4 : 1,
        maxLines: isBody ? 8 : 1,
        decoration: InputDecoration(
            labelText: '${f['label']}', border: const OutlineInputBorder()),
      ),
    );
  }

  Widget _resultCard(BuildContext context, Map<String, dynamic> r) {
    final theme = Theme.of(context);
    final ok = r['ok'] == true;
    return Card(
      color: ok ? null : theme.colorScheme.errorContainer,
      child: ListTile(
        leading: Icon(ok ? Icons.check_circle : Icons.error_outline,
            color: ok ? Colors.green.shade600 : theme.colorScheme.error),
        title: Text('${r['answer'] ?? ''}'),
      ),
    );
  }

  Widget _errorCard(BuildContext context, String msg) {
    final theme = Theme.of(context);
    return Card(
      color: theme.colorScheme.errorContainer,
      child: ListTile(
        leading: Icon(Icons.error_outline, color: theme.colorScheme.error),
        title: Text(msg),
      ),
    );
  }

  String _confirmLabel(String action) => switch (action) {
        'create_task' => 'Create task',
        'send_customer_email' => 'Send email',
        'send_whatsapp_text' => 'Send WhatsApp',
        _ => 'Confirm',
      };

  String _titleCase(String s) =>
      s.isEmpty ? s : s[0].toUpperCase() + s.substring(1).replaceAll('_', ' ');
}

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/api_client.dart';
import '../api/report_store.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';
import '../widgets/mini_map.dart';
import 'report_capture_screen.dart';
import 'report_detail_screen.dart';
import 'task_chat_screen.dart';

/// The field worker's Task Detail — practical and task-centric: what to do, where
/// to go, the checklist to tick, the team, the budget (money-gated), the actions.
/// Reads /tasks/{id}/operational/ (computed server-side) and writes back via the
/// task actions, checklist toggles and field reports.
class TaskHubScreen extends StatefulWidget {
  const TaskHubScreen(
      {super.key, required this.api, required this.taskId, required this.name});
  final ApiClient api;
  final String taskId;
  final String name;

  @override
  State<TaskHubScreen> createState() => _TaskHubScreenState();
}

class _TaskHubScreenState extends State<TaskHubScreen> {
  final _reportStore = ReportStore();
  late Future<Map<String, dynamic>> _future = _load();
  bool _busy = false;
  int _pendingReports = 0;
  Map<String, dynamic>? _completion; // cached from the last render, for the editor
  WebSocket? _ws;
  bool _wsLive = false;
  bool _disposed = false;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    _connectWs();
    // Safety net: refresh occasionally even if a socket event is dropped.
    _poll = Timer.periodic(const Duration(seconds: 20), (_) {
      if (!_wsLive) _reload();
    });
  }

  @override
  void dispose() {
    _disposed = true;
    _poll?.cancel();
    _ws?.close();
    super.dispose();
  }

  // Listen on the task socket: a new field report (or a task event) refreshes
  // the hub live, so reports appear without leaving/reopening the screen.
  Future<void> _connectWs() async {
    if (_disposed) return;
    try {
      final uri = widget.api.wsUri('/ws/task-chat/${widget.taskId}/');
      final ws = await WebSocket.connect(uri.toString(),
              headers: {'Origin': widget.api.origin})
          .timeout(const Duration(seconds: 8));
      if (_disposed) {
        ws.close();
        return;
      }
      _ws = ws;
      _wsLive = true;
      ws.listen((data) {
        try {
          final f = jsonDecode('$data');
          if (f is Map && (f['type'] == 'report' || f['type'] == 'message')) {
            if (mounted) _reload();
          }
        } catch (_) {/* ignore */}
      }, onDone: _wsClosed, onError: (_) => _wsClosed(), cancelOnError: true);
    } catch (_) {
      _wsLive = false;
      if (!_disposed) Future.delayed(const Duration(seconds: 6), _connectWs);
    }
  }

  void _wsClosed() {
    _wsLive = false;
    _ws = null;
    if (!_disposed) Future.delayed(const Duration(seconds: 6), _connectWs);
  }

  Future<Map<String, dynamic>> _load() async {
    // Best-effort sync of any reports captured offline, then load the hub.
    try {
      await _reportStore.flush(widget.api);
    } catch (_) {/* stay offline-friendly */}
    _pendingReports = await _reportStore.pendingCount();
    return await widget.api.get('/tasks/${widget.taskId}/operational/')
        as Map<String, dynamic>;
  }

  void _reload() => setState(() { _future = _load(); });

  Future<void> _addReport() async {
    final saved = await Navigator.push<Object?>(
      context,
      MaterialPageRoute(
          builder: (_) => ReportCaptureScreen(api: widget.api, taskId: widget.taskId)),
    );
    if (saved == 'offline' && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Report saved offline — it will sync automatically.')));
    }
    if (saved != null) _reload();
  }

  Future<void> _taskAction(String path, String done) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = true);
    try {
      await widget.api.post('/tasks/${widget.taskId}/$path/');
      _reload();
      messenger.showSnackBar(SnackBar(content: Text(done)));
    } on ApiException catch (e) {
      // 409 = readiness gate refused (e.g. compliance not met); 403 = permission.
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission for that."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _toggleChecklist(Map<String, dynamic> item) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.patch('/checklist-items/${item['id']}/',
          {'is_done': !(item['is_done'] == true)});
      _reload();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission for that."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  /// Manager-only: choose which requirements gate this task's completion.
  Future<void> _editRequirements() async {
    var comp = _completion;
    if (comp == null) {
      try {
        final d = await _load();
        comp = (d['completion'] as Map?)?.cast<String, dynamic>();
      } catch (_) {/* fall through to empty */}
    }
    final available =
        (comp?['available'] as List? ?? const []).cast<Map<String, dynamic>>();
    final selected = <String>{
      for (final e in (comp?['enabled'] as List? ?? const [])) '$e'
    };
    if (!mounted) return;

    final saved = await showModalBottomSheet<bool>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSt) => SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 4, 20, 20),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Align(
                alignment: Alignment.centerLeft,
                child: Text('Completion requirements',
                    style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700, color: kInk)),
              ),
              const SizedBox(height: 2),
              const Align(
                alignment: Alignment.centerLeft,
                child: Text('This task can’t be completed until these are done.',
                    style: TextStyle(fontSize: 13, color: kMuted)),
              ),
              const SizedBox(height: 10),
              for (final r in available)
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  activeColor: kBrand,
                  title: Text('${r['label']}', style: const TextStyle(fontSize: 15)),
                  value: selected.contains('${r['key']}'),
                  onChanged: (v) => setSt(() {
                    if (v == true) {
                      selected.add('${r['key']}');
                    } else {
                      selected.remove('${r['key']}');
                    }
                  }),
                ),
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                height: 50,
                child: FilledButton(
                    onPressed: () => Navigator.pop(ctx, true),
                    child: const Text('Save')),
              ),
            ]),
          ),
        ),
      ),
    );
    if (saved != true || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.patch('/tasks/${widget.taskId}/',
          {'completion_requirements': selected.toList()});
      _reload();
      messenger.showSnackBar(const SnackBar(content: Text('Requirements updated')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission to configure this."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  Future<void> _navigate(Map<String, dynamic> task) async {
    final lat = task['site_latitude'];
    final lng = task['site_longitude'];
    final site = '${task['site'] ?? ''}'.trim();
    Uri? uri;
    if (lat != null && lng != null) {
      uri = Uri.parse('https://www.google.com/maps/search/?api=1&query=$lat,$lng');
    } else if (site.isNotEmpty) {
      uri = Uri.parse(
          'https://www.google.com/maps/search/?api=1&query=${Uri.encodeComponent(site)}');
    }
    if (uri != null && await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.name),
        scrolledUnderElevation: 1,
        actions: [
          if (widget.api.canManageExecution)
            IconButton(
              tooltip: 'Completion requirements',
              icon: const Icon(Icons.rule),
              onPressed: _editRequirements,
            ),
          IconButton(
            tooltip: 'Task chat',
            icon: const Icon(Icons.forum_outlined),
            onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => TaskChatScreen(
                    api: widget.api, taskId: widget.taskId, taskName: widget.name))),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _addReport,
        icon: const Icon(Icons.add_location_alt),
        label: const Text('Report'),
      ),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => _reload(),
        child: FutureBuilder<Map<String, dynamic>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator(color: kBrand));
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 100),
                const Icon(Icons.cloud_off, size: 44, color: kMuted),
                const SizedBox(height: 12),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
              ]);
            }
            return _content(context, snap.data!);
          },
        ),
      ),
    );
  }

  Widget _content(BuildContext context, Map<String, dynamic> d) {
    final task = (d['task'] as Map).cast<String, dynamic>();
    final status = '${task['status'] ?? ''}';
    final fin = (d['financials'] as Map?)?.cast<String, dynamic>();
    final checklist = (d['checklist'] as List? ?? const []).cast<Map<String, dynamic>>();
    final subtasks = (d['subtasks'] as List? ?? const []).cast<Map<String, dynamic>>();
    final reports = (d['reports'] as List? ?? const []).cast<Map<String, dynamic>>();
    final timeline = (d['timeline'] as List? ?? const []).cast<Map<String, dynamic>>();
    final team = (d['team'] as Map?)?.cast<String, dynamic>() ?? const {};
    final desc = '${task['description'] ?? ''}'.trim();
    final site = '${task['site'] ?? ''}'.trim();

    final completion = (d['completion'] as Map?)?.cast<String, dynamic>();
    _completion = completion; // cache for the requirements editor

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 96),
      children: [
        if (_pendingReports > 0) ...[
          _pendingBanner(),
          const SizedBox(height: 12),
        ],
        _metaHeader(context, task),
        const SizedBox(height: 16),
        _primaryAction(context, status, completion),

        if (desc.isNotEmpty) ...[
          const SizedBox(height: 22),
          _sectionTitle('What you need to do'),
          _card(Text(desc, style: const TextStyle(fontSize: 14.5, height: 1.4, color: kInk))),
        ],

        if (site.isNotEmpty || task['site_latitude'] != null) ...[
          const SizedBox(height: 22),
          _sectionTitle('Location'),
          _locationCard(context, task, site),
          if (MiniMap.tryFrom(task['site_latitude'], task['site_longitude'],
                  height: 150) !=
              null) ...[
            const SizedBox(height: 8),
            MiniMap.tryFrom(task['site_latitude'], task['site_longitude'],
                height: 150)!,
          ],
        ],

        if (checklist.isNotEmpty || subtasks.isNotEmpty) ...[
          const SizedBox(height: 22),
          _sectionTitle('Checklist'),
          _checklistCard(context, checklist, subtasks),
        ],

        if (_teamNames(team).isNotEmpty) ...[
          const SizedBox(height: 22),
          _sectionTitle('Team'),
          _teamCard(context, team),
        ],

        if (fin != null) ...[
          const SizedBox(height: 22),
          _sectionTitle('Budget'),
          _money(context, fin),
        ],

        const SizedBox(height: 22),
        Row(children: [
          Expanded(child: _sectionTitle('Field reports  ·  ${reports.length}')),
          TextButton.icon(
              onPressed: _addReport,
              icon: const Icon(Icons.add, size: 18),
              label: const Text('Add')),
        ]),
        if (reports.isEmpty)
          _card(const Text('No reports yet — capture progress, a photo, a purchase '
              'or a time event with the Report button.',
              style: TextStyle(fontSize: 13, color: kMuted)))
        else
          _card(Column(children: [
            for (int i = 0; i < reports.length; i++) ...[
              if (i > 0) const Divider(height: 1),
              _ReportTile(
                  report: reports[i],
                  onTap: () => _openReport(reports[i])),
            ],
          ])),

        if (timeline.isNotEmpty) ...[
          const SizedBox(height: 22),
          _sectionTitle('Activity'),
          _card(Column(children: [
            for (int i = 0; i < timeline.length; i++)
              _TimelineTile(
                  event: timeline[i], isLast: i == timeline.length - 1),
          ])),
        ],
      ],
    );
  }

  void _openReport(Map<String, dynamic> report) async {
    final changed = await Navigator.of(context).push<bool>(MaterialPageRoute(
        builder: (_) => ReportDetailScreen(
            api: widget.api, report: report, taskName: widget.name)));
    if (changed == true) _reload();
  }

  // ── Header (status + priority + meta) ─────────────────────────────────────
  Widget _metaHeader(BuildContext context, Map<String, dynamic> task) {
    final (c, label) = _statusStyle('${task['status']}');
    final priority = '${task['priority'] ?? ''}';
    final meta = [
      if ('${task['client_name'] ?? ''}'.isNotEmpty) '${task['client_name']}',
      if ('${task['site'] ?? ''}'.isNotEmpty) '${task['site']}',
    ].join('  ·  ');
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Wrap(spacing: 8, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center,
          children: [
        _pill(label, c),
        if (priority.isNotEmpty && priority != 'medium')
          _pill('${priority[0].toUpperCase()}${priority.substring(1)} priority',
              priority == 'high' || priority == 'urgent' ? kRed : kMuted),
        DueChip('${task['due_date'] ?? ''}'),
      ]),
      if (meta.isNotEmpty) ...[
        const SizedBox(height: 10),
        Text(meta, style: const TextStyle(fontSize: 13, color: kMuted)),
      ],
    ]);
  }

  // ── Primary action ────────────────────────────────────────────────────────
  Widget _primaryAction(BuildContext context, String status,
      Map<String, dynamic>? completion) {
    final canWork = widget.api.canExecuteWork;
    final canStart = {'ready', 'assigned', 'accepted', 'waiting'}.contains(status);
    final inProgress = status == 'in_progress';
    final done = {'completed', 'closed', 'cancelled'}.contains(status);

    if (done) {
      return _banner(kGreen, Icons.check_circle,
          status == 'cancelled' ? 'Task cancelled' : 'Task completed');
    }
    if (!canWork) {
      // A viewer without work permission — no execution controls.
      return const SizedBox.shrink();
    }
    if (canStart) {
      return SizedBox(
        height: 54,
        child: FilledButton.icon(
          onPressed: _busy ? null : () => _taskAction('start', 'Task started'),
          icon: _busy
              ? const SizedBox(width: 18, height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : const Icon(Icons.play_arrow),
          label: const Text('Start task', style: TextStyle(fontSize: 16)),
        ),
      );
    }
    if (status == 'paused') {
      return Column(children: [
        _banner(kOrange, Icons.pause_circle_outline, 'Task paused'),
        const SizedBox(height: 10),
        SizedBox(
          height: 54,
          width: double.infinity,
          child: FilledButton.icon(
            onPressed: _busy ? null : () => _taskAction('resume', 'Task resumed'),
            icon: const Icon(Icons.play_arrow),
            label: const Text('Resume task', style: TextStyle(fontSize: 16)),
          ),
        ),
      ]);
    }
    if (inProgress) {
      final reqs = (completion?['requirements'] as List? ?? const [])
          .cast<Map<String, dynamic>>();
      final canComplete = completion == null || completion['ok'] == true;
      return Column(children: [
        SizedBox(
          height: 54,
          width: double.infinity,
          child: FilledButton.icon(
            onPressed: _addReport,
            icon: const Icon(Icons.assignment_turned_in),
            label: const Text('Report progress', style: TextStyle(fontSize: 16)),
          ),
        ),
        if (reqs.isNotEmpty) ...[
          const SizedBox(height: 12),
          _requirementsCard(reqs, canComplete),
        ],
        const SizedBox(height: 10),
        Row(children: [
          Expanded(
            child: SizedBox(
              height: 50,
              child: OutlinedButton.icon(
                onPressed: _busy ? null : () => _taskAction('pause', 'Task paused'),
                icon: const Icon(Icons.pause, size: 18),
                label: const Text('Pause'),
                style: OutlinedButton.styleFrom(
                    foregroundColor: kOrange, side: const BorderSide(color: kLine)),
              ),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            flex: 2,
            child: SizedBox(
              height: 50,
              child: OutlinedButton.icon(
                onPressed: (_busy || !canComplete)
                    ? null
                    : () => _taskAction('complete', 'Task completed'),
                icon: const Icon(Icons.check),
                label: Text(canComplete ? 'Complete' : 'Finish the steps first'),
                style: OutlinedButton.styleFrom(
                    foregroundColor: canComplete ? kBrandDark : kMuted,
                    side: BorderSide(color: canComplete ? kBrand : kLine)),
              ),
            ),
          ),
        ]),
      ]);
    }
    return const SizedBox.shrink();
  }

  Widget _requirementsCard(List<Map<String, dynamic>> reqs, bool ok) {
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
          color: ok ? kGreen.withOpacity(0.06) : kOrange.withOpacity(0.07),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
              color: ok ? kGreen.withOpacity(0.3) : kOrange.withOpacity(0.35))),
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(ok ? 'Ready to complete' : 'Before you can complete',
            style: TextStyle(
                fontSize: 12.5, fontWeight: FontWeight.w700,
                color: ok ? kGreen : kOrange)),
        const SizedBox(height: 8),
        for (final r in reqs)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 3),
            child: Row(children: [
              Icon(r['met'] == true ? Icons.check_circle : Icons.radio_button_unchecked,
                  size: 18, color: r['met'] == true ? kGreen : kMuted),
              const SizedBox(width: 8),
              Expanded(
                child: Text('${r['label']}',
                    style: TextStyle(
                        fontSize: 13.5,
                        color: r['met'] == true ? kMuted : kInk,
                        decoration:
                            r['met'] == true ? TextDecoration.lineThrough : null)),
              ),
            ]),
          ),
      ]),
    );
  }

  Widget _pendingBanner() => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
            color: kOrange.withOpacity(0.10),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: kOrange.withOpacity(0.3))),
        child: Row(children: [
          const Icon(Icons.sync, color: kOrange, size: 18),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
                '$_pendingReports report${_pendingReports == 1 ? '' : 's'} saved '
                'offline — pending sync.',
                style: const TextStyle(fontSize: 13, color: kInk)),
          ),
        ]),
      );

  Widget _banner(Color c, IconData icon, String text) => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
            color: c.withOpacity(0.10),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: c.withOpacity(0.3))),
        child: Row(children: [
          Icon(icon, color: c),
          const SizedBox(width: 12),
          Text(text,
              style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: c)),
        ]),
      );

  // ── Location ──────────────────────────────────────────────────────────────
  Widget _locationCard(BuildContext context, Map<String, dynamic> task, String site) {
    return _card(Row(children: [
      Container(
        width: 42, height: 42,
        decoration: BoxDecoration(
            color: kBrandTint, borderRadius: BorderRadius.circular(11)),
        child: const Icon(Icons.place_outlined, color: kBrandDark),
      ),
      const SizedBox(width: 12),
      Expanded(
        child: Text(site.isEmpty ? 'Site location set' : site,
            style: const TextStyle(fontSize: 14, color: kInk)),
      ),
      const SizedBox(width: 8),
      FilledButton.icon(
        onPressed: () => _navigate(task),
        icon: const Icon(Icons.directions, size: 18),
        label: const Text('Navigate'),
        style: FilledButton.styleFrom(
            padding: const EdgeInsets.symmetric(horizontal: 14)),
      ),
    ]));
  }

  // ── Checklist ─────────────────────────────────────────────────────────────
  Widget _checklistCard(BuildContext context, List<Map<String, dynamic>> checklist,
      List<Map<String, dynamic>> subtasks) {
    final items = [...subtasks, ...checklist];
    final total = items.length;
    final doneCount = items.where((i) => i['is_done'] == true).length;
    final canTick = widget.api.canExecuteWork;
    return _card(Column(children: [
      Row(children: [
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: LinearProgressIndicator(
                value: total == 0 ? 0 : doneCount / total,
                minHeight: 8,
                backgroundColor: kLine,
                color: kGreen),
          ),
        ),
        const SizedBox(width: 12),
        Text('$doneCount/$total',
            style: const TextStyle(fontWeight: FontWeight.w700, color: kInk)),
      ]),
      const SizedBox(height: 6),
      for (final item in items)
        _checklistRow(context, item, canTick),
    ]));
  }

  Widget _checklistRow(BuildContext context, Map<String, dynamic> item, bool canTick) {
    final done = item['is_done'] == true;
    final text = '${item['label'] ?? item['name'] ?? ''}';
    return InkWell(
      onTap: canTick ? () => _toggleChecklist(item) : null,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 10),
        child: Row(children: [
          Icon(done ? Icons.check_circle : Icons.radio_button_unchecked,
              color: done ? kGreen : kBorderDot, size: 22),
          const SizedBox(width: 12),
          Expanded(
            child: Text(text,
                style: TextStyle(
                    fontSize: 14.5,
                    color: done ? kMuted : kInk,
                    decoration: done ? TextDecoration.lineThrough : null)),
          ),
        ]),
      ),
    );
  }

  // ── Team ──────────────────────────────────────────────────────────────────
  List<String> _teamNames(Map<String, dynamic> team) =>
      [for (final v in team.values) ...((v as List?) ?? const []).map((e) => '$e')];

  Widget _teamCard(BuildContext context, Map<String, dynamic> team) {
    const roleLabels = {
      'owner': 'Task manager', 'executor': 'Team', 'approver': 'Approver',
      'watcher': 'Watching',
    };
    final rows = <Widget>[];
    team.forEach((role, names) {
      final list = ((names as List?) ?? const []).map((e) => '$e').toList();
      if (list.isEmpty) return;
      rows.add(Padding(
        padding: const EdgeInsets.symmetric(vertical: 7),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(
            width: 96,
            child: Text(roleLabels[role] ?? role,
                style: const TextStyle(fontSize: 12.5, color: kMuted)),
          ),
          Expanded(
            child: Text(list.join(', '),
                style: const TextStyle(fontSize: 14, color: kInk)),
          ),
        ]),
      ));
    });
    return _card(Column(children: rows));
  }

  // ── Budget (money-gated; null = withheld) ─────────────────────────────────
  Widget _money(BuildContext context, Map<String, dynamic> fin) {
    final over = fin['over_budget'] == true;
    return _card(Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Expanded(child: _stat('Allocated', widget.api.money(fin['allocated']))),
        Expanded(child: _stat('Spent', widget.api.money(fin['spent']))),
        Expanded(
            child: _stat('Remaining', widget.api.money(fin['remaining']),
                color: over ? kRed : kGreen)),
      ]),
      if ((fin['materials_count'] as int? ?? 0) > 0) ...[
        const Divider(height: 24),
        Text('Materials: ${widget.api.money(fin['materials_total'])} '
            '(${fin['materials_count']} item(s))',
            style: const TextStyle(fontSize: 13, color: kMuted)),
      ],
    ]));
  }

  // ── shared bits ───────────────────────────────────────────────────────────
  Widget _card(Widget child) => Container(
        width: double.infinity,
        decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: kLine)),
        padding: const EdgeInsets.all(16),
        child: child,
      );

  Widget _pill(String text, Color c) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
            color: c.withOpacity(0.13), borderRadius: BorderRadius.circular(8)),
        child: Text(text,
            style: TextStyle(color: c, fontSize: 12, fontWeight: FontWeight.w700)),
      );

  Widget _stat(String label, String value, {Color? color}) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: const TextStyle(fontSize: 11.5, color: kMuted)),
      const SizedBox(height: 3),
      Text(value,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
              fontSize: 15, fontWeight: FontWeight.w700, color: color ?? kInk)),
    ]);
  }

  Widget _sectionTitle(String t) => Padding(
        padding: const EdgeInsets.only(bottom: 8, left: 2),
        child: Text(t.toUpperCase(),
            style: const TextStyle(
                fontSize: 11.5, fontWeight: FontWeight.w700,
                letterSpacing: 0.6, color: kMuted)),
      );

  (Color, String) _statusStyle(String s) => switch (s) {
        'in_progress' => (kInfo, 'In progress'),
        'paused' => (kOrange, 'Paused'),
        'blocked' => (kRed, 'Blocked'),
        'completed' || 'closed' => (kGreen, 'Completed'),
        'cancelled' => (kMuted, 'Cancelled'),
        'ready' || 'assigned' || 'accepted' => (kOrange, 'Ready to start'),
        _ => (kOrange, 'To do'),
      };
}

IconData _reportIcon(String kind) => switch (kind) {
      'fuel' => Icons.local_gas_station,
      'material' => Icons.inventory_2,
      'meal' => Icons.restaurant,
      'expense' => Icons.receipt_long,
      'time_event' => Icons.schedule,
      'progress' => Icons.trending_up,
      _ => Icons.notes,
    };

String _shortTime(dynamic iso) {
  final t = DateTime.tryParse('$iso')?.toLocal();
  if (t == null) return '';
  return '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}

Widget _reportStatusDot(String status) {
  final (Color c, String label) = switch (status) {
    'approved' => (kGreen, 'Approved'),
    'returned' => (kOrange, 'Returned'),
    _ => (kInfo, status),
  };
  return Container(
    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 1),
    decoration: BoxDecoration(
        color: c.withOpacity(0.13), borderRadius: BorderRadius.circular(6)),
    child: Text(label,
        style: TextStyle(fontSize: 10.5, color: c, fontWeight: FontWeight.w700)),
  );
}

class _ReportTile extends StatelessWidget {
  const _ReportTile({required this.report, this.onTap});
  final Map<String, dynamic> report;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final flagged = report['location_flagged'] == true;
    final amount = report['amount'];
    final hasAmount = amount != null && '$amount' != '0.00' && '$amount' != '0';
    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      onTap: onTap,
      leading: Icon(_reportIcon('${report['kind']}'), color: flagged ? kRed : kBrand),
      title: Text('${report['title']}'),
      subtitle: Row(children: [
        Flexible(
          child: Text([
            '${report['kind_display'] ?? report['kind']}',
            if (report['employee_name'] != null &&
                '${report['employee_name']}'.isNotEmpty) '${report['employee_name']}',
            if (_shortTime(report['reported_at']).isNotEmpty)
              _shortTime(report['reported_at']),
          ].join(' · '), overflow: TextOverflow.ellipsis),
        ),
        if ('${report['status']}' != 'submitted' && '${report['status']}'.isNotEmpty) ...[
          const SizedBox(width: 6),
          _reportStatusDot('${report['status']}'),
        ],
      ]),
      trailing: Row(mainAxisSize: MainAxisSize.min, children: [
        if (hasAmount)
          Text('R $amount', style: const TextStyle(fontWeight: FontWeight.bold))
        else if (flagged)
          const Icon(Icons.warning_amber, color: kRed),
        if (onTap != null) const Icon(Icons.chevron_right, color: kMuted, size: 18),
      ]),
    );
  }
}

class _TimelineTile extends StatelessWidget {
  const _TimelineTile({required this.event, this.isLast = false});
  final Map<String, dynamic> event;
  final bool isLast;

  @override
  Widget build(BuildContext context) {
    final when = DateTime.tryParse('${event['when']}')?.toLocal();
    return IntrinsicHeight(
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Column(children: [
          Container(width: 9, height: 9, margin: const EdgeInsets.only(top: 4),
              decoration: const BoxDecoration(color: kBrand, shape: BoxShape.circle)),
          if (!isLast)
            Expanded(child: Container(width: 1.5, color: kLine)),
        ]),
        const SizedBox(width: 12),
        Expanded(
          child: Padding(
            padding: EdgeInsets.only(bottom: isLast ? 0 : 14),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${event['label']}',
                  style: const TextStyle(
                      fontSize: 13.5, fontWeight: FontWeight.w600, color: kInk)),
              if ('${event['detail'] ?? ''}'.isNotEmpty)
                Text('${event['detail']}',
                    style: const TextStyle(fontSize: 12.5, color: kMuted)),
              if (when != null)
                Text(
                    '${when.day}/${when.month} '
                    '${when.hour.toString().padLeft(2, '0')}:'
                    '${when.minute.toString().padLeft(2, '0')}',
                    style: const TextStyle(fontSize: 11.5, color: kBorderDot)),
            ]),
          ),
        ),
      ]),
    );
  }
}

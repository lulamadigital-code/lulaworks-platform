import 'package:flutter/material.dart';

import '../api/api_client.dart';
import 'report_capture_screen.dart';

/// The task's operational hub — the mobile answer to "who, where, how much, what
/// evidence". Reads /tasks/{id}/operational/ (computed server-side) and lets the
/// worker add a field report (fuel / material / time / progress) with GPS.
class TaskHubScreen extends StatefulWidget {
  const TaskHubScreen({super.key, required this.api, required this.taskId, required this.name});
  final ApiClient api;
  final String taskId;
  final String name;

  @override
  State<TaskHubScreen> createState() => _TaskHubScreenState();
}

class _TaskHubScreenState extends State<TaskHubScreen> {
  late Future<Map<String, dynamic>> _future = _load();

  Future<Map<String, dynamic>> _load() async =>
      await widget.api.get('/tasks/${widget.taskId}/operational/')
          as Map<String, dynamic>;

  Future<void> _addReport() async {
    final saved = await Navigator.push<bool>(
      context,
      MaterialPageRoute(
        builder: (_) => ReportCaptureScreen(api: widget.api, taskId: widget.taskId),
      ),
    );
    if (saved == true) setState(() { _future = _load(); });
  }

  Future<void> _taskAction(String path, String done) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('/tasks/${widget.taskId}/$path/');
      setState(() { _future = _load(); });
      messenger.showSnackBar(SnackBar(content: Text(done)));
    } on ApiException catch (e) {
      // 409 = the readiness gate refused (e.g. compliance not met) — show why.
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission for that."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  Widget _statusActions(BuildContext context, String status) {
    if (!widget.api.canManageExecution) return const SizedBox.shrink();
    final canStart =
        {'ready', 'assigned', 'accepted', 'waiting'}.contains(status);
    final canComplete = status == 'in_progress';
    if (!canStart && !canComplete) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Row(children: [
        if (canStart)
          Expanded(
            child: FilledButton.icon(
              onPressed: () => _taskAction('start', 'Task started'),
              icon: const Icon(Icons.play_arrow),
              label: const Text('Start task'),
            ),
          ),
        if (canComplete)
          Expanded(
            child: FilledButton.icon(
              onPressed: () => _taskAction('complete', 'Task completed'),
              icon: const Icon(Icons.check),
              label: const Text('Complete task'),
            ),
          ),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.name)),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _addReport,
        icon: const Icon(Icons.add_location_alt),
        label: const Text('Report'),
      ),
      body: RefreshIndicator(
        onRefresh: () async => setState(() { _future = _load(); }),
        child: FutureBuilder<Map<String, dynamic>>(
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
            final fin = (d['financials'] as Map).cast<String, dynamic>();
            final reports = (d['reports'] as List).cast<Map<String, dynamic>>();
            final timeline = (d['timeline'] as List).cast<Map<String, dynamic>>();
            final outstanding = (d['outstanding'] as List).cast<dynamic>();
            final status = '${(d['task'] as Map?)?['status'] ?? ''}';
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _statusActions(context, status),
                _money(context, fin),
                const SizedBox(height: 8),
                Row(children: [
                  _chip(context, Icons.description, '${d['documents']} docs'),
                  const SizedBox(width: 8),
                  if ((d['flagged_count'] as int? ?? 0) > 0)
                    _chip(context, Icons.warning_amber,
                        '${d['flagged_count']} flagged', color: Colors.red),
                  const SizedBox(width: 8),
                  _chip(context, Icons.checklist,
                      '${outstanding.length} outstanding'),
                ]),
                const SizedBox(height: 20),
                _sectionTitle(context, 'Field reports (${reports.length})'),
                if (reports.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 8),
                    child: Text('No reports yet — tap Report to capture one.'),
                  ),
                ...reports.map((r) => _ReportTile(report: r)),
                const SizedBox(height: 20),
                _sectionTitle(context, 'Timeline'),
                ...timeline.reversed.map((e) => _TimelineTile(event: e)),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _money(BuildContext context, Map<String, dynamic> fin) {
    final over = fin['over_budget'] == true;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
            _stat(context, 'Allocated', widget.api.money(fin['allocated'])),
            _stat(context, 'Spent', widget.api.money(fin['spent'])),
            _stat(context, 'Remaining', widget.api.money(fin['remaining']),
                color: over ? Colors.red : Colors.green),
          ]),
          if ((fin['materials_count'] as int? ?? 0) > 0) ...[
            const Divider(height: 24),
            Text('Materials: ${widget.api.money(fin['materials_total'])} '
                '(${fin['materials_count']} item(s))',
                style: Theme.of(context).textTheme.bodyMedium),
          ],
        ]),
      ),
    );
  }

  Widget _stat(BuildContext context, String label, String value, {Color? color}) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: Theme.of(context).textTheme.labelSmall),
      const SizedBox(height: 2),
      Text(value,
          style: Theme.of(context).textTheme.titleMedium?.copyWith(
              color: color, fontWeight: FontWeight.bold)),
    ]);
  }

  Widget _chip(BuildContext context, IconData icon, String label, {Color? color}) {
    return Chip(
      visualDensity: VisualDensity.compact,
      avatar: Icon(icon, size: 16, color: color),
      label: Text(label, style: TextStyle(color: color)),
    );
  }

  Widget _sectionTitle(BuildContext context, String t) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(t, style: Theme.of(context).textTheme.titleMedium),
      );
}

class _ReportTile extends StatelessWidget {
  const _ReportTile({required this.report});
  final Map<String, dynamic> report;

  @override
  Widget build(BuildContext context) {
    final flagged = report['location_flagged'] == true;
    final amount = report['amount'];
    final hasAmount = amount != null && '$amount' != '0.00' && '$amount' != '0';
    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      leading: Icon(_iconFor('${report['kind']}'),
          color: flagged ? Colors.red : Theme.of(context).colorScheme.primary),
      title: Text('${report['title']}'),
      subtitle: Text([
        '${report['kind_display'] ?? report['kind']}',
        if (report['employee_name'] != null &&
            '${report['employee_name']}'.isNotEmpty) '${report['employee_name']}',
        if (report['distance_m'] != null) '${report['distance_m']} m from site',
      ].join(' · ')),
      trailing: hasAmount
          ? Text('R $amount',
              style: const TextStyle(fontWeight: FontWeight.bold))
          : (flagged ? const Icon(Icons.warning_amber, color: Colors.red) : null),
    );
  }

  IconData _iconFor(String kind) => switch (kind) {
        'fuel' => Icons.local_gas_station,
        'material' => Icons.inventory_2,
        'expense' => Icons.receipt_long,
        'time_event' => Icons.schedule,
        'progress' => Icons.trending_up,
        _ => Icons.notes,
      };
}

class _TimelineTile extends StatelessWidget {
  const _TimelineTile({required this.event});
  final Map<String, dynamic> event;

  @override
  Widget build(BuildContext context) {
    final when = DateTime.tryParse('${event['when']}');
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Padding(
          padding: EdgeInsets.only(top: 4, right: 10),
          child: Icon(Icons.circle, size: 8),
        ),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${event['label']}',
                style: const TextStyle(fontWeight: FontWeight.w600)),
            if ('${event['detail']}'.isNotEmpty)
              Text('${event['detail']}',
                  style: Theme.of(context).textTheme.bodySmall),
            if (when != null)
              Text(
                  '${when.day}/${when.month}/${when.year} '
                  '${when.hour.toString().padLeft(2, '0')}:'
                  '${when.minute.toString().padLeft(2, '0')}',
                  style: Theme.of(context).textTheme.labelSmall),
          ]),
        ),
      ]),
    );
  }
}

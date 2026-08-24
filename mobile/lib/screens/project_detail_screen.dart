import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import 'projects_screen.dart' show StatusChip;
import 'task_hub_screen.dart';

/// Project detail — identity, the work-readiness gate, tasks, and the interactive
/// compliance checklist. Restyled to match Home/Profile; behaviour (readiness,
/// compliance submit/approve/reject) is unchanged and backend-gated.
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
    final id = widget.project.id;
    final results = await Future.wait([
      widget.api.get('/projects/$id/readiness/').catchError((_) => null),
      widget.api.get('/compliance-items/?project=$id').catchError((_) => null),
      widget.api.get('/tasks/?project=$id').catchError((_) => null),
    ]);
    return _Detail(
      readiness: results[0] is Map
          ? Readiness.fromJson((results[0] as Map).cast<String, dynamic>())
          : null,
      checklist: pageResults(results[1]),
      tasks: pageResults(results[2]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.project;
    return Scaffold(
      appBar: AppBar(title: Text(p.number), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => _reload(),
        child: FutureBuilder<_Detail>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator(color: kBrand));
            }
            final d = snap.data;
            return ListView(
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
              children: [
                _header(context),
                const SizedBox(height: 18),
                if (d?.readiness != null) ...[
                  _GateCard(readiness: d!.readiness!),
                  const SizedBox(height: 22),
                ],
                _sectionLabel('Tasks', trailing: '${d?.tasks.length ?? 0}'),
                const SizedBox(height: 10),
                if ((d?.tasks ?? const []).isEmpty)
                  _emptyRow(Icons.task_alt, 'No tasks yet')
                else
                  ...d!.tasks.map((t) => _taskCard(context, t)),
                const SizedBox(height: 22),
                _sectionLabel('Compliance',
                    trailing: '${d?.checklist.length ?? 0}'),
                const SizedBox(height: 10),
                if ((d?.checklist ?? const []).isEmpty)
                  _emptyRow(Icons.verified_user_outlined, 'No compliance items')
                else
                  _group(d!.checklist
                      .map((c) => _ChecklistTile(
                          api: widget.api, item: c, onChanged: _reload))
                      .toList()),
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
      Row(children: [
        Expanded(
          child: Text(p.title.isEmpty ? p.clientName : p.title,
              style: const TextStyle(
                  fontSize: 21, fontWeight: FontWeight.w700, color: kInk,
                  letterSpacing: -0.3)),
        ),
        const SizedBox(width: 8),
        StatusChip(status: p.status),
      ]),
      const SizedBox(height: 6),
      Text([p.clientName, p.site, p.workType]
              .where((s) => s.isNotEmpty).join('  ·  '),
          style: const TextStyle(fontSize: 13, color: kMuted)),
    ]);
  }

  Widget _taskCard(BuildContext context, Map<String, dynamic> t) {
    final (Color c, String label) = _taskStatus('${t['status']}');
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => TaskHubScreen(
                api: widget.api, taskId: '${t['id']}', name: '${t['name']}'),
          )),
          child: Container(
            decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: kLine)),
            padding: const EdgeInsets.fromLTRB(14, 13, 10, 13),
            child: Row(children: [
              Container(width: 9, height: 9,
                  decoration: BoxDecoration(color: c, shape: BoxShape.circle)),
              const SizedBox(width: 12),
              Expanded(
                child: Text('${t['name']}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 14, fontWeight: FontWeight.w500, color: kInk)),
              ),
              const SizedBox(width: 8),
              Text(label,
                  style: TextStyle(
                      fontSize: 12, fontWeight: FontWeight.w600, color: c)),
              const Icon(Icons.chevron_right, size: 19, color: kMuted),
            ]),
          ),
        ),
      ),
    );
  }

  (Color, String) _taskStatus(String s) => switch (s) {
        'in_progress' => (kInfo, 'In progress'),
        'blocked' => (kRed, 'Blocked'),
        'completed' || 'closed' => (kGreen, 'Done'),
        _ => (kOrange, 'To do'),
      };

  Widget _sectionLabel(String s, {String? trailing}) => Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(s.toUpperCase(),
              style: const TextStyle(
                  fontSize: 11.5, fontWeight: FontWeight.w700,
                  letterSpacing: 0.6, color: kMuted)),
          if (trailing != null)
            Text(trailing,
                style: const TextStyle(
                    fontSize: 12.5, fontWeight: FontWeight.w600, color: kMuted)),
        ],
      );

  Widget _group(List<Widget> tiles) {
    final children = <Widget>[];
    for (var i = 0; i < tiles.length; i++) {
      if (i > 0) children.add(const Divider(height: 1));
      children.add(tiles[i]);
    }
    return Container(
      decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: kLine)),
      child: Column(children: children),
    );
  }

  Widget _emptyRow(IconData icon, String text) => Container(
        decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: kLine)),
        padding: const EdgeInsets.symmetric(vertical: 18, horizontal: 16),
        child: Row(children: [
          Icon(icon, size: 20, color: kMuted),
          const SizedBox(width: 12),
          Text(text, style: const TextStyle(fontSize: 13.5, color: kMuted)),
        ]),
      );
}

class _Detail {
  _Detail({required this.readiness, required this.checklist, required this.tasks});
  final Readiness? readiness;
  final List<Map<String, dynamic>> checklist;
  final List<Map<String, dynamic>> tasks;
}

/// The Work Readiness gate.
class _GateCard extends StatelessWidget {
  const _GateCard({required this.readiness});
  final Readiness readiness;

  @override
  Widget build(BuildContext context) {
    final open = readiness.open;
    final overridden = readiness.gateStatus == 'overridden';
    final color = open ? (overridden ? kOrange : kGreen) : kRed;
    final label = open
        ? (overridden ? 'Ready (overridden)' : 'Ready for site')
        : 'Not ready for site';
    return Container(
      decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: kLine)),
      padding: const EdgeInsets.all(18),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Container(
            width: 42,
            height: 42,
            decoration: BoxDecoration(
                color: color.withOpacity(0.12),
                borderRadius: BorderRadius.circular(12)),
            child: Icon(open ? Icons.verified_user : Icons.gpp_bad,
                color: color, size: 22),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(label,
                style: TextStyle(
                    fontSize: 16, fontWeight: FontWeight.w700, color: color)),
          ),
          Text('${readiness.overall}%',
              style: const TextStyle(
                  fontSize: 22, fontWeight: FontWeight.w700, color: kInk)),
        ]),
        const SizedBox(height: 14),
        ClipRRect(
          borderRadius: BorderRadius.circular(5),
          child: LinearProgressIndicator(
            value: readiness.overall / 100,
            minHeight: 8,
            color: color,
            backgroundColor: kBg,
          ),
        ),
        if (readiness.categories.isNotEmpty) const SizedBox(height: 16),
        ...readiness.categories.entries.map((e) => Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(children: [
                SizedBox(
                    width: 104,
                    child: Text(_cap(e.key),
                        style: const TextStyle(fontSize: 12.5, color: kInk))),
                Expanded(
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: LinearProgressIndicator(
                      value: ((e.value as num).toDouble()) / 100,
                      minHeight: 6,
                      color: kBrand,
                      backgroundColor: kBg,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                SizedBox(
                    width: 34,
                    child: Text('${e.value}%',
                        textAlign: TextAlign.right,
                        style: const TextStyle(fontSize: 12, color: kMuted))),
              ]),
            )),
        if (readiness.blocking.isNotEmpty) ...[
          const Divider(height: 24),
          Text('Blocking (${readiness.blocking.length})',
              style: TextStyle(
                  color: color, fontWeight: FontWeight.w600, fontSize: 13)),
          const SizedBox(height: 6),
          ...readiness.blocking.map((b) => Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text('•  ${b['name']}  (${b['source']})',
                    style: const TextStyle(fontSize: 12.5, color: kMuted)),
              )),
        ],
      ]),
    );
  }

  String _cap(String s) => s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);
}

/// Interactive compliance item (behaviour unchanged; restyled).
class _ChecklistTile extends StatelessWidget {
  const _ChecklistTile(
      {required this.api, required this.item, required this.onChanged});
  final ApiClient api;
  final Map<String, dynamic> item;
  final VoidCallback onChanged;

  String get _status => '${item['status']}';
  String get _id => '${item['id']}';

  @override
  Widget build(BuildContext context) {
    final mandatory = item['is_mandatory'] == true;
    final (IconData icon, Color color) = _visual();
    return ListTile(
      onTap: () => _openActions(context),
      leading: Icon(icon, color: color, size: 22),
      title: Text('${item['name']}',
          style: const TextStyle(
              fontSize: 14, fontWeight: FontWeight.w500, color: kInk)),
      subtitle: Text('${item['category']} · ${item['status']}'
          '${mandatory ? ' · mandatory' : ''}',
          style: const TextStyle(fontSize: 12, color: kMuted)),
      trailing: const Icon(Icons.more_horiz, color: kMuted),
    );
  }

  (IconData, Color) _visual() {
    switch (_status) {
      case 'approved':
        return item['is_satisfied'] == true
            ? (Icons.check_circle, kGreen)
            : (Icons.error, kOrange);
      case 'submitted':
        return (Icons.hourglass_top, kOrange);
      case 'rejected':
        return (Icons.cancel, kRed);
      case 'expired':
        return (Icons.event_busy, kRed);
      default:
        return (Icons.radio_button_unchecked, kMuted);
    }
  }

  void _openActions(BuildContext context) {
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
              padding: const EdgeInsets.fromLTRB(20, 4, 20, 2),
              child: Text('${item['name']}',
                  style: const TextStyle(
                      fontSize: 16, fontWeight: FontWeight.w600, color: kInk)),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
              child: Text('Currently: ${item['status']}',
                  style: const TextStyle(fontSize: 12.5, color: kMuted)),
            ),
            const Divider(height: 1),
            for (final a in actions)
              ListTile(
                leading: Icon(a.icon, color: a.destructive ? kRed : kBrandDark),
                title: Text(a.label,
                    style: TextStyle(color: a.destructive ? kRed : kInk)),
                onTap: () {
                  Navigator.pop(sheetCtx);
                  _run(context, a);
                },
              ),
            if (actions.isEmpty)
              const Padding(
                padding: EdgeInsets.all(20),
                child: Text('No actions available to you for this item.',
                    style: TextStyle(color: kMuted)),
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
              : e.message)));
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

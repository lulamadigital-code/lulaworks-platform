import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';

class ProjectDetailScreen extends StatefulWidget {
  const ProjectDetailScreen({super.key, required this.api, required this.project});
  final ApiClient api;
  final Project project;

  @override
  State<ProjectDetailScreen> createState() => _ProjectDetailScreenState();
}

class _ProjectDetailScreenState extends State<ProjectDetailScreen> {
  late Future<_Detail> _future = _load();

  Future<_Detail> _load() async {
    final readiness = Readiness.fromJson(
        await widget.api.get('/projects/${widget.project.id}/readiness/')
            as Map<String, dynamic>);
    final checklist =
        pageResults(await widget.api.get('/compliance-items/?project=${widget.project.id}'));
    return _Detail(readiness, checklist);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.project.number)),
      body: RefreshIndicator(
        onRefresh: () async => setState(() => _future = _load()),
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
                Text('Compliance checklist',
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 8),
                ...d.checklist.map((c) => _ChecklistTile(item: c)),
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
  _Detail(this.readiness, this.checklist);
  final Readiness readiness;
  final List<Map<String, dynamic>> checklist;
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

class _ChecklistTile extends StatelessWidget {
  const _ChecklistTile({required this.item});
  final Map<String, dynamic> item;

  @override
  Widget build(BuildContext context) {
    final satisfied = item['is_satisfied'] == true;
    final mandatory = item['is_mandatory'] == true;
    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      leading: Icon(
        satisfied ? Icons.check_circle : Icons.radio_button_unchecked,
        color: satisfied ? Colors.green : Theme.of(context).colorScheme.outline,
        size: 20,
      ),
      title: Text('${item['name']}'),
      subtitle: Text('${item['category']} · ${item['status']}'
          '${mandatory ? ' · mandatory' : ''}'),
    );
  }
}

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/brand_logo.dart';
import 'my_tasks_screen.dart';
import 'notifications_screen.dart';
import 'project_detail_screen.dart';
import 'projects_screen.dart' show StatusChip;

/// The Home tab — a field-first snapshot of the company's work: how many jobs
/// are active, what needs attention, and a quick way into the rest of the app.
/// Counts only (no money), so it is safe for every role under the Golden Rule.
class DashboardScreen extends StatefulWidget {
  const DashboardScreen({
    super.key,
    required this.api,
    required this.onOpenProjects,
    required this.onOpenLulama,
  });

  final ApiClient api;
  final VoidCallback onOpenProjects;
  final VoidCallback onOpenLulama;

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  late Future<_Dashboard> _future = _load();

  Future<_Dashboard> _load() async {
    final api = widget.api;
    // Projects is the one call that must succeed; the rest are best-effort so a
    // hiccup never blanks the page. The money call only fires for users who may
    // see money (finance.view_money) — a worker's dashboard never requests it.
    final results = await Future.wait([
      api.get('/projects/'),
      api.get('/tasks/').catchError((_) => null),
      api.get('/me/').catchError((_) => null),
      api.canViewMoney
          ? api.get('/finance/commercial-dashboard/').catchError((_) => null)
          : Future<dynamic>.value(null),
    ]);
    return _Dashboard(
      projects: pageResults(results[0]).map(Project.fromJson).toList(),
      tasks: pageResults(results[1]),
      me: results[2] is Map
          ? (results[2] as Map).cast<String, dynamic>()
          : const <String, dynamic>{},
      finance: results[3] is Map
          ? (results[3] as Map).cast<String, dynamic>()
          : null,
    );
  }

  String _money(dynamic v) {
    final n = double.tryParse('$v') ?? 0;
    final parts = n.toStringAsFixed(2).split('.');
    final whole = parts[0]
        .replaceAllMapped(RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]} ');
    return '${widget.api.currencySymbol} $whole.${parts[1]}';
  }

  Future<void> _refresh() async {
    setState(() { _future = _load(); });
    await _future;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const BrandLogo(height: 26),
        actions: [_NotificationBell(api: widget.api)],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: FutureBuilder<_Dashboard>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 100),
                Icon(Icons.cloud_off,
                    size: 48, color: Theme.of(context).colorScheme.outline),
                const SizedBox(height: 12),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
                const SizedBox(height: 16),
                Center(
                    child: OutlinedButton(
                        onPressed: _refresh, child: const Text('Retry'))),
              ]);
            }
            final d = snap.data!;
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _greeting(context, d),
                const SizedBox(height: 16),
                _myTasksCard(context),
                const SizedBox(height: 12),
                if (d.finance != null) ...[
                  _moneyCard(context, d.finance!),
                  const SizedBox(height: 12),
                ],
                _statsGrid(context, d),
                const SizedBox(height: 24),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text('Your projects',
                        style: Theme.of(context).textTheme.titleMedium),
                    TextButton(
                        onPressed: widget.onOpenProjects,
                        child: const Text('See all')),
                  ],
                ),
                if (d.projects.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 8),
                    child: Text('No projects yet.'),
                  ),
                ...d.projects.take(4).map((p) => _ProjectTile(api: widget.api, project: p)),
                const SizedBox(height: 16),
                if (widget.api.canGenerateAi)
                  FilledButton.tonalIcon(
                    onPressed: widget.onOpenLulama,
                    icon: const Icon(Icons.auto_awesome),
                    label: const Text('Ask Lulama'),
                  ),
                const SizedBox(height: 24),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _greeting(BuildContext context, _Dashboard d) {
    final name = (d.me['user'] as Map?)?['first_name']?.toString() ?? '';
    final company =
        (d.me['active_company'] as Map?)?['name']?.toString() ?? '';
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(name.isEmpty ? 'Welcome back' : 'Hi $name',
          style: Theme.of(context).textTheme.headlineSmall),
      if (company.isNotEmpty)
        Text(company,
            style: Theme.of(context)
                .textTheme
                .bodyMedium
                ?.copyWith(color: Theme.of(context).colorScheme.outline)),
    ]);
  }

  Widget _myTasksCard(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => MyTasksScreen(api: widget.api))),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(children: [
            Container(
              width: 46,
              height: 46,
              decoration: BoxDecoration(
                  color: scheme.primary.withOpacity(0.10),
                  borderRadius: BorderRadius.circular(11)),
              child: Icon(Icons.checklist_rtl, color: scheme.primary),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('My tasks',
                    style: Theme.of(context)
                        .textTheme
                        .titleMedium
                        ?.copyWith(fontWeight: FontWeight.bold)),
                Text('Work assigned to you',
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: scheme.onSurfaceVariant)),
              ]),
            ),
            const Icon(Icons.chevron_right),
          ]),
        ),
      ),
    );
  }

  Widget _moneyCard(BuildContext context, Map<String, dynamic> fin) {
    final scheme = Theme.of(context).colorScheme;
    final overdue = double.tryParse('${fin['overdue']}') ?? 0;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(children: [
          Container(
            width: 46,
            height: 46,
            decoration: BoxDecoration(
                color: scheme.primary.withOpacity(0.10),
                borderRadius: BorderRadius.circular(11)),
            child: Icon(Icons.account_balance_wallet, color: scheme.primary),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Outstanding',
                  style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: scheme.onSurfaceVariant)),
              Text(_money(fin['outstanding_invoiced']),
                  style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.bold)),
            ]),
          ),
          if (overdue > 0)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(
                  color: kRed.withOpacity(0.12),
                  borderRadius: BorderRadius.circular(12)),
              child: Text('${_money(fin['overdue'])} overdue',
                  style: const TextStyle(
                      color: kRed, fontSize: 12, fontWeight: FontWeight.w600)),
            ),
        ]),
      ),
    );
  }

  Widget _statsGrid(BuildContext context, _Dashboard d) {
    final tiles = [
      _StatTile(
        icon: Icons.work_outline,
        label: 'Active jobs',
        value: d.activeProjects,
        onTap: widget.onOpenProjects,
      ),
      _StatTile(
        icon: Icons.play_circle_outline,
        label: 'In progress',
        value: d.inProgress,
      ),
      // The one tile that carries an alarm state — colour communicates status.
      _StatTile(
        icon: Icons.warning_amber,
        label: 'Blocked',
        value: d.blocked,
        alertColor: d.blocked > 0 ? kRed : null,
      ),
      _StatTile(
        icon: Icons.checklist,
        label: 'Open tasks',
        value: d.openTasks,
      ),
    ];
    return GridView.count(
      crossAxisCount: 2,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      mainAxisSpacing: 12,
      crossAxisSpacing: 12,
      childAspectRatio: 1.55,
      children: tiles,
    );
  }
}

class _StatTile extends StatelessWidget {
  const _StatTile({
    required this.icon,
    required this.label,
    required this.value,
    this.alertColor,
    this.onTap,
  });

  final IconData icon;
  final String label;
  final int value;
  final Color? alertColor; // set only when the count carries a status meaning
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    // Icon carries a quiet brand/status tint; the number stays neutral ink so
    // colour means status, not decoration.
    final iconColor = alertColor ?? scheme.primary;
    final valueColor = alertColor ?? scheme.onSurface;
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Icon(icon, color: iconColor, size: 22),
              Row(
                crossAxisAlignment: CrossAxisAlignment.baseline,
                textBaseline: TextBaseline.alphabetic,
                children: [
                  Text('$value',
                      style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                          fontWeight: FontWeight.bold, color: valueColor)),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(label,
                        style: Theme.of(context).textTheme.bodySmall),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ProjectTile extends StatelessWidget {
  const _ProjectTile({required this.api, required this.project});
  final ApiClient api;
  final Project project;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: CircleAvatar(
        backgroundColor: project.isReady
            ? Colors.green.shade100
            : Theme.of(context).colorScheme.surfaceVariant,
        child: Icon(project.isReady ? Icons.check : Icons.hourglass_bottom,
            color: project.isReady ? Colors.green.shade800 : null, size: 20),
      ),
      title: Text(project.title.isEmpty ? project.number : project.title,
          maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text('${project.number} · ${project.clientName}',
          maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: StatusChip(status: project.status),
      onTap: () => Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => ProjectDetailScreen(api: api, project: project),
      )),
    );
  }
}

/// Computed snapshot backing the dashboard.
/// App-bar bell with an unread badge. Fetches its own count so it stays correct
/// after visiting the notifications screen, independent of the dashboard load.
class _NotificationBell extends StatefulWidget {
  const _NotificationBell({required this.api});
  final ApiClient api;

  @override
  State<_NotificationBell> createState() => _NotificationBellState();
}

class _NotificationBellState extends State<_NotificationBell> {
  int _count = 0;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    try {
      final d = await widget.api.get('/notifications/unread/');
      if (mounted && d is Map) setState(() => _count = d['count'] as int? ?? 0);
    } catch (_) {/* ignore */}
  }

  @override
  Widget build(BuildContext context) {
    return IconButton(
      tooltip: 'Notifications',
      icon: Badge(
        isLabelVisible: _count > 0,
        label: Text('$_count'),
        child: const Icon(Icons.notifications_outlined),
      ),
      onPressed: () async {
        await Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => NotificationsScreen(api: widget.api)));
        _refresh();
      },
    );
  }
}

class _Dashboard {
  _Dashboard({
    required this.projects,
    required this.tasks,
    required this.me,
    this.finance,
  });

  final List<Project> projects;
  final List<Map<String, dynamic>> tasks;
  final Map<String, dynamic> me;
  final Map<String, dynamic>? finance;

  static const _openStatuses = {
    'draft', 'ready', 'assigned', 'accepted', 'waiting'
  };

  int get activeProjects =>
      projects.where((p) => p.status == 'ready' || p.status == 'in_execution').length;
  int _count(bool Function(String) test) =>
      tasks.where((t) => test('${t['status']}')).length;

  int get inProgress => _count((s) => s == 'in_progress');
  int get blocked => _count((s) => s == 'blocked');
  int get openTasks => _count(_openStatuses.contains);
}

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../nav/app_nav.dart';
import '../theme.dart';
import '../widgets/brand_logo.dart';
import 'customer_form_screen.dart';
import 'customers_screen.dart';
import 'my_tasks_screen.dart';
import 'notifications_screen.dart';
import 'project_detail_screen.dart';
import 'projects_screen.dart' show StatusChip;
import 'quotations_screen.dart';
import 'task_hub_screen.dart';

/// The Home command centre. Permission-adaptive and driven entirely by live
/// backend data — every number is real; nothing is faked. Answers, at a glance:
/// what needs me, what's happening, my jobs and my tasks.
class DashboardScreen extends StatefulWidget {
  const DashboardScreen({
    super.key,
    required this.api,
    required this.onOpenProjects,
    required this.onOpenLulaAi,
  });

  final ApiClient api;
  final VoidCallback onOpenProjects;
  final VoidCallback onOpenLulaAi;

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

const _openStatuses = {'draft', 'ready', 'assigned', 'accepted', 'waiting'};
const _doneStatuses = {'completed', 'closed'};
const _pendingQuote = {
  'draft', 'review', 'manager_approval', 'commercial_approval'
};

class _DashboardScreenState extends State<DashboardScreen>
    with SingleTickerProviderStateMixin {
  late Future<_Home> _future = _load();
  late final AnimationController _pulse = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 900))
    ..repeat(reverse: true);

  ApiClient get api => widget.api;

  Future<_Home> _load() async {
    // /me is critical (drives the error state); the rest degrade gracefully.
    final me = (await api.get('/me/')) as Map;
    final r = await Future.wait([
      api.get('/projects/').catchError((_) => null),
      api.get('/tasks/?mine=1').catchError((_) => null),
      api.get('/notifications/').catchError((_) => null),
      api.get('/notifications/unread/').catchError((_) => null),
      api.canSeeQuotes
          ? api.get('/quotations/').catchError((_) => null)
          : Future<dynamic>.value(null),
      api.canViewMoney
          ? api.get('/finance/commercial-dashboard/').catchError((_) => null)
          : Future<dynamic>.value(null),
    ]);
    final unreadCount = r[3] is Map ? (r[3]['count'] as int? ?? 0) : 0;
    api.unread.value = unreadCount; // seed the live badge
    return _Home(
      me: me.cast<String, dynamic>(),
      projects: pageResults(r[0]).map(Project.fromJson).toList(),
      tasks: pageResults(r[1]),
      notifications: pageResults(r[2]),
      unread: unreadCount,
      quotations: pageResults(r[4]),
      finance: r[5] is Map ? (r[5] as Map).cast<String, dynamic>() : null,
    );
  }

  Future<void> _refresh() async {
    setState(() { _future = _load(); });
    await _future;
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  void _push(Widget screen) =>
      Navigator.of(context).push(MaterialPageRoute(builder: (_) => screen));

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: RefreshIndicator(
          color: kBrand,
          onRefresh: _refresh,
          child: FutureBuilder<_Home>(
            future: _future,
            builder: (context, snap) {
              if (snap.connectionState == ConnectionState.waiting) {
                return _LoadingView(pulse: _pulse);
              }
              if (snap.hasError) {
                return _ErrorView(onRetry: _refresh, error: snap.error);
              }
              return _content(context, snap.data!);
            },
          ),
        ),
      ),
    );
  }

  Widget _content(BuildContext context, _Home h) {
    final attention = _attention(h);
    final jobs = _activeJobs(h);
    final today = _todaysTasks(h);
    final activity = h.notifications.take(5).toList();

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
      children: [
        _header(context, h),
        const SizedBox(height: 20),
        _quickActions(context, h),
        const SizedBox(height: 24),

        // MY WORK
        _sectionHeader(context, 'My work',
            actionLabel: attention.isEmpty ? null : 'My tasks',
            onAction: () => _push(MyTasksScreen(api: api))),
        const SizedBox(height: 10),
        if (attention.isEmpty)
          _allCaughtUp(context)
        else
          ...attention.map((a) => _attentionCard(context, a)),
        const SizedBox(height: 24),

        // OVERVIEW
        _sectionHeader(context, 'Overview'),
        const SizedBox(height: 12),
        _kpiGrid(context, h),
        const SizedBox(height: 24),

        // ACTIVE JOBS
        _sectionHeader(context, 'Active jobs',
            actionLabel: 'View all', onAction: widget.onOpenProjects),
        const SizedBox(height: 10),
        if (jobs.isEmpty)
          _emptyRow(context, Icons.work_outline, 'No active jobs',
              'Your active projects will appear here.')
        else
          ...jobs.map((p) => _jobCard(context, p)),
        const SizedBox(height: 24),

        // TODAY'S TASKS
        _sectionHeader(context, "Today's tasks",
            actionLabel: 'My tasks',
            onAction: () => _push(MyTasksScreen(api: api))),
        const SizedBox(height: 10),
        if (today.isEmpty)
          _emptyRow(context, Icons.task_alt, "You're all caught up",
              'No tasks need your attention today.')
        else
          _card(Column(children: [
            for (int i = 0; i < today.length; i++) ...[
              if (i > 0) const Divider(height: 1),
              _taskRow(context, today[i]),
            ],
          ])),
        const SizedBox(height: 24),

        // RECENT ACTIVITY
        if (activity.isNotEmpty) ...[
          _sectionHeader(context, 'Recent activity',
              actionLabel: 'View all',
              onAction: () => _push(NotificationsScreen(api: api))),
          const SizedBox(height: 10),
          _card(Column(children: [
            for (int i = 0; i < activity.length; i++) ...[
              if (i > 0) const Divider(height: 1),
              _activityRow(context, activity[i]),
            ],
          ])),
        ],
      ],
    );
  }

  // ── Header ──────────────────────────────────────────────────────────────
  Widget _header(BuildContext context, _Home h) {
    final user = (h.me['user'] as Map?) ?? const {};
    final first = '${user['first_name'] ?? ''}'.trim();
    final company = '${(h.me['active_company'] as Map?)?['name'] ?? ''}'.trim();
    final hour = DateTime.now().hour;
    final greet = hour < 12
        ? 'Good morning'
        : hour < 17
            ? 'Good afternoon'
            : 'Good evening';
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        const BrandLogo(height: 24),
        const Spacer(),
        _bell(context, h.unread),
      ]),
      const SizedBox(height: 18),
      Text(first.isEmpty ? greet : '$greet, $first',
          style: const TextStyle(
              fontSize: 24, fontWeight: FontWeight.w700, color: kInk,
              letterSpacing: -0.4),
          maxLines: 1, overflow: TextOverflow.ellipsis),
      const SizedBox(height: 3),
      Text(_subtitle(company),
          style: const TextStyle(fontSize: 13.5, color: kMuted),
          maxLines: 1, overflow: TextOverflow.ellipsis),
    ]);
  }

  /// The Home command centre reads differently per persona:
  ///   owner   → business ("what's happening at Acme")
  ///   manager → operations ("what your team is working on")
  ///   employee→ personal ("your work for today") — never company money.
  String _subtitle(String company) {
    switch (personaFor(api)) {
      case AppPersona.employee:
        return "Here's your work for today.";
      case AppPersona.manager:
        return company.isEmpty
            ? "Here's what your team is working on."
            : "Here's what your team is working on at $company.";
      case AppPersona.owner:
        return company.isEmpty
            ? "Here's what needs your attention today."
            : "Here's what's happening at $company.";
    }
  }

  Widget _bell(BuildContext context, int unread) {
    return InkResponse(
      radius: 24,
      onTap: () async {
        await Navigator.of(context).push(
            MaterialPageRoute(builder: (_) => NotificationsScreen(api: api)));
        _refresh(); // refresh the unread badge after reading
      },
      child: Container(
        width: 42,
        height: 42,
        decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: kLine)),
        child: ValueListenableBuilder<int>(
          valueListenable: api.unread,
          builder: (_, live, __) => Badge(
            isLabelVisible: live > 0,
            label: Text('$live'),
            offset: const Offset(-6, 6),
            child: const Icon(Icons.notifications_none, size: 21, color: kInk),
          ),
        ),
      ),
    );
  }

  // ── Quick actions ───────────────────────────────────────────────────────
  Widget _quickActions(BuildContext context, _Home h) {
    final actions = <_QA>[
      if (api.canManageCustomers)
        _QA('New customer', Icons.person_add_alt,
            () => _push(CustomerFormScreen(api: api))),
      if (api.canSeeQuotes)
        _QA('Quotations', Icons.request_quote_outlined,
            () => _push(QuotationsScreen(api: api))),
      _QA('My tasks', Icons.task_alt, () => _push(MyTasksScreen(api: api))),
      if (api.canSeeCustomers)
        _QA('Customers', Icons.business_outlined,
            () => _push(CustomersScreen(api: api))),
      if (api.canGenerateAi)
        _QA('Ask LulaAI', Icons.auto_awesome_outlined, widget.onOpenLulaAi),
    ];
    return SizedBox(
      height: 84,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        physics: const BouncingScrollPhysics(),
        itemCount: actions.length,
        separatorBuilder: (_, __) => const SizedBox(width: 10),
        itemBuilder: (context, i) => _quickAction(context, actions[i]),
      ),
    );
  }

  Widget _quickAction(BuildContext context, _QA a) {
    return SizedBox(
      width: 78,
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: a.onTap,
        child: Column(children: [
          Container(
            width: 52,
            height: 52,
            decoration: BoxDecoration(
                color: kBrandTint, borderRadius: BorderRadius.circular(14)),
            child: Icon(a.icon, color: kBrandDark, size: 24),
          ),
          const SizedBox(height: 6),
          Text(a.label,
              textAlign: TextAlign.center,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 11.5, height: 1.1, color: kInk)),
        ]),
      ),
    );
  }

  // ── My work / attention ─────────────────────────────────────────────────
  List<_Attn> _attention(_Home h) {
    final out = <_Attn>[];
    final today = _todayPrefix();
    // Overdue tasks (due before today, not finished).
    final overdue = h.tasks.where((t) {
      final due = '${t['due_date'] ?? ''}';
      final s = '${t['status']}';
      return due.isNotEmpty && due.compareTo(today) < 0 &&
          !_doneStatuses.contains(s);
    }).toList();
    if (overdue.isNotEmpty) {
      out.add(_Attn(
        Icons.warning_amber_rounded,
        kRed,
        '${overdue.length} overdue task${overdue.length == 1 ? '' : 's'}',
        overdue.first['name']?.toString() ?? '',
        'Review',
        () => _push(MyTasksScreen(api: api)),
      ));
    }
    // Blocked tasks assigned to me.
    final blocked = h.tasks.where((t) => '${t['status']}' == 'blocked').toList();
    if (blocked.isNotEmpty) {
      out.add(_Attn(
        Icons.block,
        kOrange,
        '${blocked.length} blocked task${blocked.length == 1 ? '' : 's'}',
        blocked.first['name']?.toString() ?? '',
        'Open',
        () => _push(MyTasksScreen(api: api)),
      ));
    }
    // Quotations pending review.
    if (api.canSeeQuotes) {
      final pending =
          h.quotations.where((q) => _pendingQuote.contains('${q['status']}')).toList();
      if (pending.isNotEmpty) {
        out.add(_Attn(
          Icons.request_quote_outlined,
          kBrandDark,
          '${pending.length} quotation${pending.length == 1 ? '' : 's'} pending',
          '${pending.first['number'] ?? ''} · ${pending.first['client_name'] ?? ''}',
          'Review',
          () => _push(QuotationsScreen(api: api)),
        ));
      }
    }
    // Overdue money.
    if (api.canViewMoney && h.finance != null) {
      final overdueAmt = double.tryParse('${h.finance!['overdue']}') ?? 0;
      if (overdueAmt > 0) {
        out.add(_Attn(
          Icons.account_balance_wallet_outlined,
          kRed,
          'Invoices overdue',
          api.money(h.finance!['overdue']),
          'View',
          widget.onOpenProjects,
        ));
      }
    }
    return out.take(4).toList();
  }

  Widget _attentionCard(BuildContext context, _Attn a) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: a.onTap,
          child: Container(
            decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: kLine)),
            padding: const EdgeInsets.fromLTRB(14, 14, 12, 14),
            child: Row(children: [
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                    color: a.color.withOpacity(0.12),
                    borderRadius: BorderRadius.circular(11)),
                child: Icon(a.icon, color: a.color, size: 21),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(a.title,
                          style: const TextStyle(
                              fontSize: 14.5,
                              fontWeight: FontWeight.w600,
                              color: kInk)),
                      if (a.subtitle.isNotEmpty) ...[
                        const SizedBox(height: 2),
                        Text(a.subtitle,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontSize: 12.5, color: kMuted)),
                      ],
                    ]),
              ),
              const SizedBox(width: 8),
              Row(mainAxisSize: MainAxisSize.min, children: [
                Text(a.action,
                    style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                        color: kBrandDark)),
                const Icon(Icons.chevron_right, size: 18, color: kBrandDark),
              ]),
            ]),
          ),
        ),
      ),
    );
  }

  Widget _allCaughtUp(BuildContext context) {
    return _card(Padding(
      padding: const EdgeInsets.symmetric(vertical: 22),
      child: Column(children: [
        Container(
          width: 46,
          height: 46,
          decoration: const BoxDecoration(
              color: kBrandTint, shape: BoxShape.circle),
          child: const Icon(Icons.check, color: kBrandDark, size: 24),
        ),
        const SizedBox(height: 10),
        const Text("You're all caught up",
            style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: kInk)),
        const SizedBox(height: 2),
        const Text('Nothing needs your attention right now.',
            style: TextStyle(fontSize: 12.5, color: kMuted)),
      ]),
    ));
  }

  // ── KPI grid ────────────────────────────────────────────────────────────
  Widget _kpiGrid(BuildContext context, _Home h) {
    final active = h.projects
        .where((p) => p.status == 'ready' || p.status == 'in_execution')
        .length;
    final myOpen =
        h.tasks.where((t) => _openStatuses.contains('${t['status']}')).length;
    final inProg =
        h.tasks.where((t) => '${t['status']}' == 'in_progress').length;
    final done =
        h.tasks.where((t) => _doneStatuses.contains('${t['status']}')).length;

    // Employees get a purely personal scorecard — their tasks, never company
    // money or company-wide job counts (the Golden Rule).
    if (personaFor(api) == AppPersona.employee) {
      final today = _todayPrefix();
      final dueToday = h.tasks.where((t) {
        final s = '${t['status']}';
        if (_doneStatuses.contains(s)) return false;
        return '${t['due_date'] ?? ''}' == today;
      }).length;
      final personal = <_Kpi>[
        _Kpi('My tasks', '$myOpen', Icons.task_alt,
            onTap: () => _push(MyTasksScreen(api: api))),
        _Kpi('Due today', '$dueToday', Icons.today_outlined,
            onTap: () => _push(MyTasksScreen(api: api))),
        _Kpi('In progress', '$inProg', Icons.play_circle_outline),
        _Kpi('Completed', '$done', Icons.check_circle_outline),
      ];
      return GridView.count(
        crossAxisCount: 2,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        mainAxisSpacing: 12,
        crossAxisSpacing: 12,
        childAspectRatio: 1.75,
        children: [for (final k in personal) _kpiTile(context, k)],
      );
    }

    final tiles = <_Kpi>[
      _Kpi('Active jobs', '$active', Icons.work_outline, onTap: widget.onOpenProjects),
      _Kpi('Open tasks', '$myOpen', Icons.task_alt,
          onTap: () => _push(MyTasksScreen(api: api))),
    ];
    if (api.canSeeQuotes) {
      final pending =
          h.quotations.where((q) => _pendingQuote.contains('${q['status']}')).length;
      tiles.add(_Kpi('Pending quotes', '$pending', Icons.request_quote_outlined,
          onTap: () => _push(QuotationsScreen(api: api))));
    } else {
      tiles.add(_Kpi('In progress', '$inProg', Icons.play_circle_outline));
    }
    if (api.canViewMoney && h.finance != null) {
      tiles.add(_Kpi('Outstanding',
          api.money(h.finance!['outstanding_invoiced']),
          Icons.account_balance_wallet_outlined,
          accent: true, small: true, onTap: widget.onOpenProjects));
    } else {
      tiles.add(_Kpi('Completed', '$done', Icons.check_circle_outline));
    }

    return GridView.count(
      crossAxisCount: 2,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      mainAxisSpacing: 12,
      crossAxisSpacing: 12,
      childAspectRatio: 1.75,
      children: [for (final k in tiles) _kpiTile(context, k)],
    );
  }

  Widget _kpiTile(BuildContext context, _Kpi k) {
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: k.onTap,
        child: Container(
          decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: kLine)),
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Container(
                width: 34,
                height: 34,
                decoration: BoxDecoration(
                    color: (k.accent ? kBrand : kInk).withOpacity(0.06),
                    borderRadius: BorderRadius.circular(9)),
                child: Icon(k.icon,
                    size: 18, color: k.accent ? kBrandDark : kMuted),
              ),
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(k.value,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                        fontSize: k.small ? 20 : 26,
                        fontWeight: FontWeight.w700,
                        letterSpacing: -0.5,
                        color: k.accent ? kBrandDark : kInk)),
                Text(k.label,
                    style: const TextStyle(fontSize: 12.5, color: kMuted)),
              ]),
            ],
          ),
        ),
      ),
    );
  }

  // ── Active jobs ─────────────────────────────────────────────────────────
  List<Project> _activeJobs(_Home h) {
    final active = h.projects
        .where((p) => p.status == 'ready' || p.status == 'in_execution')
        .toList();
    final rest = h.projects
        .where((p) => !(p.status == 'ready' || p.status == 'in_execution'))
        .toList();
    return [...active, ...rest].take(4).toList();
  }

  Widget _jobCard(BuildContext context, Project p) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: () => _push(ProjectDetailScreen(api: api, project: p)),
          child: Container(
            decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: kLine)),
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  Expanded(
                    child: Text(
                        p.title.isEmpty ? p.number : p.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.w600,
                            color: kInk)),
                  ),
                  const SizedBox(width: 8),
                  StatusChip(status: p.status),
                ]),
                const SizedBox(height: 4),
                Row(children: [
                  const Icon(Icons.business, size: 13, color: kMuted),
                  const SizedBox(width: 4),
                  Flexible(
                    child: Text(
                        [p.clientName, p.site]
                            .where((s) => s.isNotEmpty)
                            .join('  ·  '),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 12.5, color: kMuted)),
                  ),
                ]),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // ── Today's tasks ───────────────────────────────────────────────────────
  List<Map<String, dynamic>> _todaysTasks(_Home h) {
    final today = _todayPrefix();
    final due = h.tasks.where((t) {
      final s = '${t['status']}';
      if (_doneStatuses.contains(s)) return false;
      final d = '${t['due_date'] ?? ''}';
      return s == 'in_progress' || (d.isNotEmpty && d.compareTo(today) <= 0);
    }).toList();
    // In-progress first, then by due date.
    due.sort((a, b) {
      final ap = '${a['status']}' == 'in_progress' ? 0 : 1;
      final bp = '${b['status']}' == 'in_progress' ? 0 : 1;
      if (ap != bp) return ap - bp;
      return '${a['due_date'] ?? ''}'.compareTo('${b['due_date'] ?? ''}');
    });
    return due.take(4).toList();
  }

  Widget _taskRow(BuildContext context, Map<String, dynamic> t) {
    final status = '${t['status']}';
    final (Color c, String label) = _taskStatus(status);
    return InkWell(
      onTap: () => _push(TaskHubScreen(
          api: api, taskId: '${t['id']}', name: '${t['name']}')),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 13),
        child: Row(children: [
          Container(width: 9, height: 9,
              decoration: BoxDecoration(color: c, shape: BoxShape.circle)),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${t['name']}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                      fontSize: 14, fontWeight: FontWeight.w500, color: kInk)),
              if ('${t['site'] ?? ''}'.isNotEmpty) ...[
                const SizedBox(height: 1),
                Text('${t['site']}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 12, color: kMuted)),
              ],
            ]),
          ),
          const SizedBox(width: 8),
          Text(label,
              style: TextStyle(
                  fontSize: 12, fontWeight: FontWeight.w600, color: c)),
          const Icon(Icons.chevron_right, size: 18, color: kMuted),
        ]),
      ),
    );
  }

  (Color, String) _taskStatus(String s) => switch (s) {
        'in_progress' => (kInfo, 'In progress'),
        'blocked' => (kRed, 'Blocked'),
        'completed' || 'closed' => (kGreen, 'Done'),
        _ => (kOrange, 'To do'),
      };

  // ── Recent activity ─────────────────────────────────────────────────────
  Widget _activityRow(BuildContext context, Map<String, dynamic> n) {
    final unread = n['is_read'] != true;
    final when = DateTime.tryParse('${n['created_at']}');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.only(top: 5),
          child: Container(width: 8, height: 8,
              decoration: BoxDecoration(
                  color: unread ? kBrand : kBorderDot, shape: BoxShape.circle)),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Text('${n['title']}',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 13.5, color: kInk)),
        ),
        const SizedBox(width: 8),
        Text(_ago(when),
            style: const TextStyle(fontSize: 11.5, color: kMuted)),
      ]),
    );
  }

  // ── Small shared bits ───────────────────────────────────────────────────
  Widget _sectionHeader(BuildContext context, String title,
      {String? actionLabel, VoidCallback? onAction}) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(title,
            style: const TextStyle(
                fontSize: 17.5, fontWeight: FontWeight.w700, color: kInk,
                letterSpacing: -0.3)),
        if (actionLabel != null)
          InkWell(
            onTap: onAction,
            borderRadius: BorderRadius.circular(8),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Text(actionLabel,
                    style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                        color: kBrandDark)),
                const Icon(Icons.chevron_right, size: 17, color: kBrandDark),
              ]),
            ),
          ),
      ],
    );
  }

  Widget _card(Widget child) => Container(
        decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: kLine)),
        padding: const EdgeInsets.symmetric(horizontal: 14),
        child: child,
      );

  Widget _emptyRow(
      BuildContext context, IconData icon, String title, String body) {
    return _card(Padding(
      padding: const EdgeInsets.symmetric(vertical: 20),
      child: Row(children: [
        Icon(icon, color: kMuted, size: 22),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title,
                style: const TextStyle(
                    fontSize: 14, fontWeight: FontWeight.w600, color: kInk)),
            Text(body, style: const TextStyle(fontSize: 12.5, color: kMuted)),
          ]),
        ),
      ]),
    ));
  }

  // ── date/time helpers ───────────────────────────────────────────────────
  String _todayPrefix() {
    final n = DateTime.now();
    return '${n.year.toString().padLeft(4, '0')}-'
        '${n.month.toString().padLeft(2, '0')}-'
        '${n.day.toString().padLeft(2, '0')}';
  }

  String _ago(DateTime? t) {
    if (t == null) return '';
    final d = DateTime.now().difference(t);
    if (d.inMinutes < 1) return 'now';
    if (d.inMinutes < 60) return '${d.inMinutes}m ago';
    if (d.inHours < 24) return '${d.inHours}h ago';
    if (d.inDays < 7) return '${d.inDays}d ago';
    return '${t.day}/${t.month}';
  }
}

// ── Model + small value types ───────────────────────────────────────────────
class _Home {
  _Home({
    required this.me,
    required this.projects,
    required this.tasks,
    required this.notifications,
    required this.unread,
    required this.quotations,
    required this.finance,
  });
  final Map<String, dynamic> me;
  final List<Project> projects;
  final List<Map<String, dynamic>> tasks;
  final List<Map<String, dynamic>> notifications;
  final int unread;
  final List<Map<String, dynamic>> quotations;
  final Map<String, dynamic>? finance;
}

class _QA {
  _QA(this.label, this.icon, this.onTap);
  final String label;
  final IconData icon;
  final VoidCallback onTap;
}

class _Attn {
  _Attn(this.icon, this.color, this.title, this.subtitle, this.action, this.onTap);
  final IconData icon;
  final Color color;
  final String title;
  final String subtitle;
  final String action;
  final VoidCallback onTap;
}

class _Kpi {
  _Kpi(this.label, this.value, this.icon,
      {this.accent = false, this.small = false, this.onTap});
  final String label;
  final String value;
  final IconData icon;
  final bool accent;
  final bool small;
  final VoidCallback? onTap;
}

// ── Loading (skeleton) ───────────────────────────────────────────────────────
class _LoadingView extends StatelessWidget {
  const _LoadingView({required this.pulse});
  final Animation<double> pulse;

  @override
  Widget build(BuildContext context) {
    Widget box(double w, double h, {double r = 8}) => _Shimmer(
        pulse: pulse,
        child: Container(
            width: w,
            height: h,
            decoration: BoxDecoration(
                color: kLine, borderRadius: BorderRadius.circular(r))));
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
      children: [
        Row(children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              box(200, 24),
              const SizedBox(height: 8),
              box(150, 14),
            ]),
          ),
          _Shimmer(pulse: pulse, child: const CircleAvatar(radius: 21, backgroundColor: kLine)),
        ]),
        const SizedBox(height: 24),
        Row(children: List.generate(4, (i) => Padding(
            padding: const EdgeInsets.only(right: 12),
            child: box(52, 52, r: 14)))),
        const SizedBox(height: 24),
        box(120, 18),
        const SizedBox(height: 12),
        box(double.infinity, 66, r: 14),
        const SizedBox(height: 10),
        box(double.infinity, 66, r: 14),
        const SizedBox(height: 24),
        box(100, 18),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(child: box(double.infinity, 84, r: 14)),
          const SizedBox(width: 12),
          Expanded(child: box(double.infinity, 84, r: 14)),
        ]),
      ],
    );
  }
}

class _Shimmer extends StatelessWidget {
  const _Shimmer({required this.pulse, required this.child});
  final Animation<double> pulse;
  final Widget child;
  @override
  Widget build(BuildContext context) => FadeTransition(
      opacity: Tween<double>(begin: 0.45, end: 0.9).animate(pulse), child: child);
}

// ── Error ────────────────────────────────────────────────────────────────────
class _ErrorView extends StatelessWidget {
  const _ErrorView({required this.onRetry, this.error});
  final Future<void> Function() onRetry;
  final Object? error;

  String get _message {
    final e = error;
    if (e is ApiException) {
      if (e.isAuth) return 'Your session expired. Please sign in again.';
      return e.message;
    }
    final s = '$e';
    if (s.contains('SocketException') || s.contains('Connection')) {
      return "Can't reach the server. Check your connection.";
    }
    return "We couldn't load your dashboard.";
  }

  @override
  Widget build(BuildContext context) {
    return ListView(padding: const EdgeInsets.symmetric(horizontal: 32), children: [
      const SizedBox(height: 140),
      const Icon(Icons.cloud_off, size: 48, color: kMuted),
      const SizedBox(height: 14),
      const Center(
        child: Text('Something went wrong',
            style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600, color: kInk)),
      ),
      const SizedBox(height: 6),
      Center(
        child: Text(_message,
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 13.5, color: kMuted)),
      ),
      const SizedBox(height: 20),
      Center(
        child: FilledButton(onPressed: onRetry, child: const Text('Try again')),
      ),
    ]);
  }
}

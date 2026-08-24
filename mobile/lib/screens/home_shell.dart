import 'package:flutter/material.dart';

import '../api/api_client.dart';
import 'dashboard_screen.dart';
import 'finance_screen.dart';
import 'lulama_screen.dart';
import 'more_screen.dart';
import 'profile_screen.dart';
import 'projects_screen.dart';

/// The signed-in app shell. Bottom nav is permission-driven and capped at five:
///   Home · Projects · [Finance] · More · Profile
/// Finance appears only with finance.view_money. Secondary modules (Customers,
/// Lulama, and more to come) live under the More tab so the bar never overflows.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key, required this.api, required this.onSignOut});
  final ApiClient api;
  final Future<void> Function() onSignOut;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  @override
  void initState() {
    super.initState();
    widget.api.refreshMe().then((_) {
      if (mounted) setState(() {});
    }).catchError((_) {/* keep cached perms */});
  }

  List<_TabDef> _tabs() {
    return [
      const _TabDef('home', 'Home', Icons.dashboard_outlined, Icons.dashboard),
      const _TabDef('projects', 'Projects', Icons.folder_outlined, Icons.folder),
      if (widget.api.canViewMoney)
        const _TabDef('finance', 'Finance', Icons.payments_outlined, Icons.payments),
      const _TabDef('more', 'More', Icons.apps_outlined, Icons.apps),
      const _TabDef('profile', 'Profile', Icons.person_outline, Icons.person),
    ];
  }

  Widget _screenFor(String id) {
    switch (id) {
      case 'home':
        return DashboardScreen(
          api: widget.api,
          onOpenProjects: () => _goto('projects'),
          onOpenLulama: _openLulama,
        );
      case 'projects':
        return ProjectsScreen(api: widget.api, onSignOut: widget.onSignOut);
      case 'finance':
        return FinanceScreen(api: widget.api);
      case 'more':
        return MoreScreen(api: widget.api);
      case 'profile':
        return ProfileScreen(api: widget.api, onSignOut: widget.onSignOut);
    }
    return const SizedBox.shrink();
  }

  void _goto(String id) {
    final i = _tabs().indexWhere((t) => t.id == id);
    if (i >= 0) setState(() => _index = i);
  }

  void _openLulama() => Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => LulamaScreen(api: widget.api)));

  @override
  Widget build(BuildContext context) {
    final tabs = _tabs();
    if (_index >= tabs.length) _index = 0;
    return Scaffold(
      body: IndexedStack(
        index: _index,
        children: [for (final t in tabs) _screenFor(t.id)],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: [
          for (final t in tabs)
            NavigationDestination(
                icon: Icon(t.icon),
                selectedIcon: Icon(t.selectedIcon),
                label: t.label),
        ],
      ),
    );
  }
}

class _TabDef {
  const _TabDef(this.id, this.label, this.icon, this.selectedIcon);
  final String id;
  final String label;
  final IconData icon;
  final IconData selectedIcon;
}

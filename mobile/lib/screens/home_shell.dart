import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../nav/app_nav.dart';
import 'lulama_screen.dart';

/// The signed-in app shell. The bottom bar is built entirely from the central,
/// permission-driven navigation config ([bottomTabsFor]) — different users get
/// a bar shaped for their job, with no role checks living here.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key, required this.api, required this.onSignOut});
  final ApiClient api;
  final Future<void> Function() onSignOut;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  late final NavActions _actions = NavActions(
    onSignOut: widget.onSignOut,
    openProjects: () => _goto('jobs'),
    openLulama: _openLulama,
  );

  @override
  void initState() {
    super.initState();
    // Resolve role/permissions on launch so the bar (and every gated surface)
    // is correct. If they changed (e.g. an admin adjusted this user's role),
    // the next refresh rebuilds the bar — no reinstall needed.
    widget.api.refreshMe().then((_) {
      if (mounted) setState(() {});
    }).catchError((_) {});
  }

  /// Switch to a tab by id (used by in-app shortcuts, e.g. the dashboard).
  /// No-op if that tab isn't part of this user's bar.
  void _goto(String id) {
    final i = bottomTabsFor(widget.api).indexWhere((t) => t.id == id);
    if (i >= 0) setState(() => _index = i);
  }

  void _openLulama() => Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => LulamaScreen(api: widget.api)));

  @override
  Widget build(BuildContext context) {
    final tabs = bottomTabsFor(widget.api);
    // Guard against a shrinking bar (permissions resolved after first paint).
    final index = _index.clamp(0, tabs.length - 1);
    return Scaffold(
      body: IndexedStack(
        index: index,
        children: [for (final t in tabs) t.build(widget.api, _actions)],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: [
          for (final t in tabs)
            NavigationDestination(
                icon: Icon(t.icon),
                selectedIcon: Icon(t.activeIcon),
                label: t.label),
        ],
      ),
    );
  }
}

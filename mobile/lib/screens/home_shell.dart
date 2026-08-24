import 'package:flutter/material.dart';

import '../api/api_client.dart';
import 'customers_screen.dart';
import 'dashboard_screen.dart';
import 'lulama_screen.dart';
import 'more_screen.dart';
import 'projects_screen.dart';
import 'purchasing_screen.dart';

/// The signed-in app shell. A fixed five-tab bottom bar gives a clear mental
/// model — Home · CRM · Jobs · Purchasing · More — with permission checks handled
/// inside each destination (and enforced by the backend).
class HomeShell extends StatefulWidget {
  const HomeShell({super.key, required this.api, required this.onSignOut});
  final ApiClient api;
  final Future<void> Function() onSignOut;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  static const _tabs = [
    ('Home', Icons.dashboard_outlined, Icons.dashboard),
    ('CRM', Icons.contacts_outlined, Icons.contacts),
    ('Jobs', Icons.work_outline, Icons.work),
    ('Purchasing', Icons.shopping_cart_outlined, Icons.shopping_cart),
    ('More', Icons.apps_outlined, Icons.apps),
  ];

  @override
  void initState() {
    super.initState();
    // Resolve role/permissions on launch so gated surfaces are correct.
    widget.api.refreshMe().then((_) {
      if (mounted) setState(() {});
    }).catchError((_) {});
  }

  void _goto(String id) {
    const map = {'home': 0, 'crm': 1, 'jobs': 2, 'purchasing': 3, 'more': 4};
    final i = map[id];
    if (i != null) setState(() => _index = i);
  }

  void _openLulama() => Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => LulamaScreen(api: widget.api)));

  @override
  Widget build(BuildContext context) {
    final screens = [
      DashboardScreen(
        api: widget.api,
        onOpenProjects: () => _goto('jobs'),
        onOpenLulama: _openLulama,
      ),
      CustomersScreen(api: widget.api),
      ProjectsScreen(api: widget.api, onSignOut: widget.onSignOut),
      PurchasingScreen(api: widget.api),
      MoreScreen(api: widget.api, onSignOut: widget.onSignOut),
    ];
    return Scaffold(
      body: IndexedStack(index: _index, children: screens),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: [
          for (final t in _tabs)
            NavigationDestination(
                icon: Icon(t.$2), selectedIcon: Icon(t.$3), label: t.$1),
        ],
      ),
    );
  }
}

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../screens/commercial_documents_screen.dart';
import '../screens/company_settings_screen.dart';
import '../screens/customers_screen.dart';
import '../screens/dashboard_screen.dart';
import '../screens/estimates_screen.dart';
import '../screens/field_home_screen.dart';
import '../screens/finance_screen.dart';
import '../screens/lulama_screen.dart';
import '../screens/more_screen.dart';
import '../screens/my_tasks_screen.dart';
import '../screens/notifications_screen.dart';
import '../screens/profile_screen.dart';
import '../screens/projects_screen.dart';
import '../screens/purchase_orders_screen.dart';
import '../screens/purchasing_screen.dart';
import '../screens/quotations_screen.dart';
import '../screens/rfq_screen.dart';
import '../screens/suppliers_screen.dart';
import '../screens/team_screen.dart';

/// ─────────────────────────────────────────────────────────────────────────
/// Central, role + permission driven navigation.
///
/// This is the ONE place that decides what a user can see. Nothing else in the
/// app hard-codes `if (role == 'employee')`. The whole bar, the More menu and
/// the Home persona are derived here from the permissions the backend returns
/// on `/me/`.
///
/// AUTHORITY: this file only shapes the *presentation*. Every screen and every
/// API call re-checks permission server-side — hiding a tab is never the
/// security boundary (see [AccessRestricted] for the fallback when a gated
/// screen is reached anyway).
/// ─────────────────────────────────────────────────────────────────────────

/// The three lived experiences. Derived from permissions, not a role string,
/// so a custom permission set still lands in the right place.
enum AppPersona {
  /// Company owner / admin — the business command centre. Has company.manage.
  owner,

  /// Manager / supervisor — the operations command centre. Runs work and
  /// approvals but does not administer the company.
  manager,

  /// Employee / groundfloor — the personal work command centre. Does the work
  /// assigned to them; never sees company money unless a permission allows it.
  employee,
}

/// Resolve the persona from live permissions (backend is authoritative).
AppPersona personaFor(ApiClient api) {
  if (api.canManageCompany) return AppPersona.owner;
  // A manager is defined by oversight — approvals and money — NOT by doing the
  // work. execution.manage (start/complete tasks, file reports) is a field
  // capability, so it must never promote a groundfloor worker to manager.
  final managerial = api.canApprovePO ||
      api.canApproveQuote ||
      api.canApproveRfq ||
      api.canApproveEstimate ||
      api.canRecordPayment || // finance.manage || invoices.approve
      api.canViewMoney || // sees company money → oversight, not field crew
      api.canInviteUsers;
  if (managerial) return AppPersona.manager;
  return AppPersona.employee;
}

/// Callbacks a destination may need from the shell.
class NavActions {
  const NavActions({
    required this.onSignOut,
    required this.openProjects,
    required this.openLulama,
  });
  final Future<void> Function() onSignOut;
  final VoidCallback openProjects;
  final VoidCallback openLulama;
}

/// One bottom-bar destination.
class NavTab {
  const NavTab({
    required this.id,
    required this.label,
    required this.icon,
    required this.activeIcon,
    required this.build,
  });
  final String id;
  final String label;
  final IconData icon;
  final IconData activeIcon;
  final Widget Function(ApiClient api, NavActions actions) build;
}

/// The ordered, permission-filtered bottom bar for this user.
///
/// Home and More are always present; up to three primary destinations sit
/// between them (Material caps the bar at five). Anything that doesn't earn a
/// slot is still reachable from More, so nothing is lost.
///
///   • owner    → Home · CRM · Jobs · Purchasing · More
///   • manager  → Home · CRM · Jobs · Purchasing · More  (each still gated)
///   • employee → Home · My Work · (CRM/Purchasing if permitted) · Jobs · More
List<NavTab> bottomTabsFor(ApiClient api) {
  final persona = personaFor(api);
  final isEmployee = persona == AppPersona.employee;

  // Candidates in priority order (earlier = kept first when capping).
  final home = NavTab(
    id: 'home',
    label: 'Home',
    icon: Icons.dashboard_outlined,
    activeIcon: Icons.dashboard,
    // Employees get the task-centric Field Home; owners/managers get the
    // command-centre dashboard.
    build: (api, a) => isEmployee
        ? FieldHomeScreen(api: api)
        : DashboardScreen(
            api: api,
            onOpenProjects: a.openProjects,
            onOpenLulama: a.openLulama,
          ),
  );
  final myWork = NavTab(
    id: 'mywork',
    label: 'My Work',
    icon: Icons.task_alt_outlined,
    activeIcon: Icons.task_alt,
    build: (api, a) => MyTasksScreen(api: api),
  );
  final crm = NavTab(
    id: 'crm',
    label: 'CRM',
    icon: Icons.contacts_outlined,
    activeIcon: Icons.contacts,
    build: (api, a) => CustomersScreen(api: api),
  );
  final jobs = NavTab(
    id: 'jobs',
    label: 'Jobs',
    icon: Icons.work_outline,
    activeIcon: Icons.work,
    build: (api, a) => ProjectsScreen(api: api, onSignOut: a.onSignOut),
  );
  final purchasing = NavTab(
    id: 'purchasing',
    label: 'Purchasing',
    icon: Icons.shopping_cart_outlined,
    activeIcon: Icons.shopping_cart,
    build: (api, a) => PurchasingScreen(api: api),
  );
  final more = NavTab(
    id: 'more',
    label: 'More',
    icon: Icons.apps_outlined,
    activeIcon: Icons.apps,
    build: (api, a) => MoreScreen(api: api, actions: a),
  );

  // Middle candidates, in the order they should fill the (max 3) slots.
  final middle = <NavTab>[
    if (isEmployee) myWork, // employees lead with their own work
    if (api.canSeeCustomers) crm,
    jobs,
    if (api.canProcurement) purchasing,
  ];

  return [home, ...middle.take(3), more];
}

/// ── More menu ────────────────────────────────────────────────────────────
/// A single, grouped entry for the More screen.
class MoreItem {
  const MoreItem({
    required this.id,
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.visible,
    required this.build,
    this.hiddenIfTab,
  });
  final String id;
  final String title;
  final String subtitle;
  final IconData icon;

  /// Permission predicate — the backend is authoritative, this only hides UI.
  final bool Function(ApiClient api) visible;

  /// If this id is already a primary tab for the user, don't repeat it here.
  final String? hiddenIfTab;

  final Widget Function(ApiClient api, NavActions actions) build;
}

class MoreGroup {
  const MoreGroup(this.label, this.items);
  final String label;
  final List<MoreItem> items;
}

/// The grouped More menu for this user. Items are dropped when the permission
/// is absent or when they already have a dedicated tab; empty groups vanish.
List<MoreGroup> moreGroupsFor(ApiClient api, Set<String> shownTabIds) {
  final groups = <MoreGroup>[
    MoreGroup('BUSINESS', [
      MoreItem(
        id: 'quotations',
        title: 'Quotations',
        subtitle: 'Quotes & approval workflow',
        icon: Icons.article_outlined,
        visible: (a) => a.canSeeQuotes,
        build: (a, _) => QuotationsScreen(api: a),
      ),
      MoreItem(
        id: 'commercial',
        title: 'Invoices & delivery',
        subtitle: 'Tax invoices & delivery notes',
        icon: Icons.receipt_long_outlined,
        visible: (a) => a.canSeeCommercial,
        build: (a, _) => CommercialDocumentsScreen(api: a),
      ),
      MoreItem(
        id: 'finance',
        title: 'Finance',
        subtitle: 'Revenue, outstanding, ageing',
        icon: Icons.payments_outlined,
        visible: (a) => a.canViewMoney,
        build: (a, _) => FinanceScreen(api: a),
      ),
    ]),
    MoreGroup('RELATIONSHIPS', [
      MoreItem(
        id: 'customers',
        title: 'Customers',
        subtitle: 'Companies, contacts & activity',
        icon: Icons.contacts_outlined,
        visible: (a) => a.canSeeCustomers,
        hiddenIfTab: 'crm',
        build: (a, _) => CustomersScreen(api: a),
      ),
    ]),
    MoreGroup('OPERATIONS', [
      MoreItem(
        id: 'mytasks',
        title: 'My tasks',
        subtitle: 'Work assigned to you',
        icon: Icons.task_alt_outlined,
        visible: (a) => true,
        hiddenIfTab: 'mywork',
        build: (a, _) => MyTasksScreen(api: a),
      ),
      MoreItem(
        id: 'suppliers',
        title: 'Suppliers',
        subtitle: 'Supplier database',
        icon: Icons.local_shipping_outlined,
        visible: (a) => a.canProcurement,
        hiddenIfTab: 'purchasing',
        build: (a, _) => SuppliersScreen(api: a),
      ),
      MoreItem(
        id: 'purchaseorders',
        title: 'Purchase orders',
        subtitle: 'Orders & receipts',
        icon: Icons.shopping_cart_outlined,
        visible: (a) => a.canProcurement,
        hiddenIfTab: 'purchasing',
        build: (a, _) => PurchaseOrdersScreen(api: a),
      ),
      MoreItem(
        id: 'rfqs',
        title: 'RFQs',
        subtitle: 'Requests for quotation',
        icon: Icons.mark_email_read_outlined,
        visible: (a) => a.canSeeRfq,
        hiddenIfTab: 'purchasing',
        build: (a, _) => RfqScreen(api: a),
      ),
      MoreItem(
        id: 'estimates',
        title: 'Estimates',
        subtitle: 'Cost estimates',
        icon: Icons.calculate_outlined,
        visible: (a) => a.canSeeEstimates,
        hiddenIfTab: 'purchasing',
        build: (a, _) => EstimatesScreen(api: a),
      ),
    ]),
    MoreGroup('ADMINISTRATION', [
      MoreItem(
        id: 'team',
        title: 'Users & employees',
        subtitle: 'Invite & manage people',
        icon: Icons.group_outlined,
        visible: (a) => a.canInviteUsers || a.canManageCompany,
        build: (a, _) => TeamScreen(api: a),
      ),
      MoreItem(
        id: 'company',
        title: 'Company profile',
        subtitle: 'Registered details & branding',
        icon: Icons.business_outlined,
        visible: (a) => a.canManageCompany,
        build: (a, _) => CompanySettingsScreen(api: a),
      ),
      MoreItem(
        id: 'ai',
        title: 'Lulaworks AI',
        subtitle: 'Ask the assistant',
        icon: Icons.auto_awesome_outlined,
        visible: (a) => a.canGenerateAi,
        build: (a, _) => LulamaScreen(api: a),
      ),
    ]),
    MoreGroup('ACCOUNT', [
      MoreItem(
        id: 'account',
        title: 'Account',
        subtitle: 'Profile, security & company',
        icon: Icons.person_outline,
        visible: (a) => true,
        build: (a, act) => ProfileScreen(api: a, onSignOut: act.onSignOut),
      ),
      MoreItem(
        id: 'notifications',
        title: 'Notifications',
        subtitle: 'Alerts & mentions',
        icon: Icons.notifications_none,
        visible: (a) => true,
        build: (a, _) => NotificationsScreen(api: a),
      ),
    ]),
  ];

  // Filter items, then drop groups that end up empty.
  final out = <MoreGroup>[];
  for (final g in groups) {
    final items = [
      for (final it in g.items)
        if (it.visible(api) &&
            !(it.hiddenIfTab != null && shownTabIds.contains(it.hiddenIfTab)))
          it
    ];
    if (items.isNotEmpty) out.add(MoreGroup(g.label, items));
  }
  return out;
}

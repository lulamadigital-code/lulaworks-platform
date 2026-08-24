import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:lulaworks_mobile/api/api_client.dart';
import 'package:lulaworks_mobile/nav/app_nav.dart';

/// Build an ApiClient whose cached /me/ carries [perms] (the real code path:
/// create() reads the 'me' pref and resolves permissions from it).
Future<ApiClient> clientWith(List<String> perms, {String role = 'Custom'}) async {
  SharedPreferences.setMockInitialValues({
    'me': jsonEncode({
      'role': role,
      'permissions': perms,
      'user': {'first_name': 'Test'},
      'active_company': {'name': 'Acme', 'currency': 'ZAR'},
    }),
  });
  return ApiClient.create();
}

List<String> tabIds(ApiClient api) =>
    [for (final t in bottomTabsFor(api)) t.id];

Set<String> moreIds(ApiClient api) {
  final shown = {for (final t in bottomTabsFor(api)) t.id};
  return {
    for (final g in moreGroupsFor(api, shown))
      for (final it in g.items) it.id
  };
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // Real backend codenames (see ApiClient getters).
  const owner = ['company.manage', 'finance.view_money', 'users.invite',
    'customers.manage', 'crm.manage', 'procurement.manage', 'quotes.create',
    'quotes.approve', 'po.approve', 'ai.generate'];
  const manager = ['quotes.approve', 'po.approve', 'customers.manage',
    'procurement.manage', 'execution.manage'];
  const fieldEmployee = ['work.edit']; // groundfloor: starts/completes tasks
  const procurementEmployee = ['work.edit', 'procurement.manage'];
  const salesEmployee = ['crm.manage']; // canSeeCustomers, no approvals

  test('owner → business command centre, full bar', () async {
    final api = await clientWith(owner);
    expect(personaFor(api), AppPersona.owner);
    expect(tabIds(api), ['home', 'crm', 'jobs', 'purchasing', 'more']);
    // Admin surfaces present in More.
    expect(moreIds(api), containsAll(['team', 'company', 'finance', 'ai']));
  });

  test('manager → operations, no company admin', () async {
    final api = await clientWith(manager);
    expect(personaFor(api), AppPersona.manager);
    expect(tabIds(api), ['home', 'crm', 'jobs', 'purchasing', 'more']);
    // Managers don't administer the company.
    expect(moreIds(api), isNot(contains('company')));
    // …but they DO review attendance corrections.
    expect(moreIds(api), contains('attendance_review'));
    // No My Work tab for managers (their work lives in Home/Jobs).
    expect(tabIds(api), isNot(contains('mywork')));
  });

  test('field employee → personal work bar, no money', () async {
    final api = await clientWith(fieldEmployee);
    expect(personaFor(api), AppPersona.employee);
    expect(tabIds(api), ['home', 'mywork', 'jobs', 'more']);
    // Golden Rule: no company money, no admin, no CRM/procurement.
    expect(api.canViewMoney, isFalse);
    final more = moreIds(api);
    expect(more, isNot(contains('finance')));
    expect(more, isNot(contains('company')));
    expect(more, isNot(contains('team')));
    expect(more, isNot(contains('customers')));
    expect(more, isNot(contains('attendance_review')));
  });

  test('procurement employee → Purchasing tab', () async {
    final api = await clientWith(procurementEmployee);
    expect(personaFor(api), AppPersona.employee);
    expect(tabIds(api), contains('purchasing'));
    expect(tabIds(api), contains('mywork'));
    expect(api.canViewMoney, isFalse);
  });

  test('sales/CRM employee → CRM tab, still an employee', () async {
    final api = await clientWith(salesEmployee);
    expect(personaFor(api), AppPersona.employee);
    expect(tabIds(api), contains('crm'));
    expect(api.canViewMoney, isFalse);
  });

  test('More never repeats a primary tab', () async {
    final api = await clientWith(owner);
    final tabs = tabIds(api).toSet();
    final more = moreIds(api);
    // CRM is a tab → customers entry suppressed; procurement is a tab → its
    // sub-items suppressed.
    expect(tabs.contains('crm') && more.contains('customers'), isFalse);
    expect(tabs.contains('purchasing') && more.contains('suppliers'), isFalse);
  });

  test('bar is always Home … More and never exceeds 5', () async {
    for (final p in [owner, manager, fieldEmployee, procurementEmployee,
      salesEmployee, <String>[]]) {
      final api = await clientWith(p);
      final ids = tabIds(api);
      expect(ids.first, 'home');
      expect(ids.last, 'more');
      expect(ids.length, lessThanOrEqualTo(5));
      expect(ids.length, greaterThanOrEqualTo(3));
    }
  });
}

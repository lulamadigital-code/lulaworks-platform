import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import 'commercial_documents_screen.dart';
import 'finance_screen.dart';
import 'lulama_screen.dart';
import 'my_tasks_screen.dart';
import 'notifications_screen.dart';
import 'profile_screen.dart';
import 'quotations_screen.dart';

/// The "More" hub — the account and everything that doesn't live in the four
/// primary tabs. Entries are permission-gated; the backend enforces the rest.
class MoreScreen extends StatelessWidget {
  const MoreScreen({super.key, required this.api, required this.onSignOut});
  final ApiClient api;
  final Future<void> Function() onSignOut;

  @override
  Widget build(BuildContext context) {
    final work = <_Entry>[
      _Entry('My tasks', 'Work assigned to you', Icons.task_alt,
          () => MyTasksScreen(api: api)),
      if (api.canSeeQuotes)
        _Entry('Quotations', 'Quotes & workflow', Icons.article_outlined,
            () => QuotationsScreen(api: api)),
      if (api.canSeeCommercial)
        _Entry('Invoices & delivery', 'Tax invoices & delivery notes',
            Icons.receipt_outlined, () => CommercialDocumentsScreen(api: api)),
      if (api.canViewMoney)
        _Entry('Finance', 'Revenue, outstanding, ageing',
            Icons.payments_outlined, () => FinanceScreen(api: api)),
    ];
    final account = <_Entry>[
      _Entry('Account', 'Profile, security, company', Icons.person_outline,
          () => ProfileScreen(api: api, onSignOut: onSignOut)),
      _Entry('Notifications', 'Alerts & mentions', Icons.notifications_none,
          () => NotificationsScreen(api: api)),
      if (api.canGenerateAi)
        _Entry('Lulaworks AI', 'Ask the assistant', Icons.auto_awesome_outlined,
            () => LulamaScreen(api: api)),
    ];
    return Scaffold(
      appBar: AppBar(title: const Text('More'), scrolledUnderElevation: 1),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 28),
        children: [
          _label('WORK'),
          const SizedBox(height: 8),
          _group(context, work),
          const SizedBox(height: 20),
          _label('ACCOUNT'),
          const SizedBox(height: 8),
          _group(context, account),
        ],
      ),
    );
  }

  Widget _label(String s) => Padding(
        padding: const EdgeInsets.only(left: 4),
        child: Text(s,
            style: const TextStyle(
                fontSize: 11.5, fontWeight: FontWeight.w700,
                letterSpacing: 0.6, color: kMuted)),
      );

  Widget _group(BuildContext context, List<_Entry> entries) {
    return Container(
      decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: kLine)),
      child: Column(children: [
        for (int i = 0; i < entries.length; i++) ...[
          if (i > 0) const Divider(height: 1, indent: 60),
          ListTile(
            contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
            leading: Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                  color: kBrand.withOpacity(0.08),
                  borderRadius: BorderRadius.circular(11)),
              child: Icon(entries[i].icon, color: kBrandDark, size: 21),
            ),
            title: Text(entries[i].title,
                style: const TextStyle(
                    fontSize: 15, fontWeight: FontWeight.w600, color: kInk)),
            subtitle: Text(entries[i].subtitle,
                style: const TextStyle(fontSize: 12.5, color: kMuted)),
            trailing: const Icon(Icons.chevron_right, color: kMuted),
            onTap: () => Navigator.of(context)
                .push(MaterialPageRoute(builder: (_) => entries[i].build())),
          ),
        ],
      ]),
    );
  }
}

class _Entry {
  _Entry(this.title, this.subtitle, this.icon, this.build);
  final String title;
  final String subtitle;
  final IconData icon;
  final Widget Function() build;
}

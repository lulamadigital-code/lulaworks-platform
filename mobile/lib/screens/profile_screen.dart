import 'package:flutter/material.dart';

import '../api/api_client.dart';
import 'company_settings_screen.dart';
import 'team_screen.dart';

/// The Profile tab — who you're signed in as, which company is active, and the
/// sign-out control. Reads /me/ (user + active_company).
class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key, required this.api, required this.onSignOut});
  final ApiClient api;
  final Future<void> Function() onSignOut;

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  late Future<Map<String, dynamic>> _future = _load();

  Future<Map<String, dynamic>> _load() async =>
      (await widget.api.get('/me/') as Map).cast<String, dynamic>();

  Future<void> _confirmSignOut() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Sign out?'),
        content: const Text('You will need to sign in again to use the app.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Sign out')),
        ],
      ),
    );
    if (ok == true) await widget.onSignOut();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Profile')),
      body: RefreshIndicator(
        onRefresh: () async => setState(() { _future = _load(); }),
        child: FutureBuilder<Map<String, dynamic>>(
          future: _future,
          builder: (context, snap) {
            final me = snap.data ?? const {};
            final user = (me['user'] as Map?)?.cast<String, dynamic>() ?? const {};
            final company =
                (me['active_company'] as Map?)?.cast<String, dynamic>() ?? const {};
            final name = '${user['full_name'] ?? ''}'.trim();
            final email = '${user['email'] ?? ''}';
            final role = '${me['role'] ?? ''}';
            final perms =
                ((me['permissions'] as List?) ?? const []).map((e) => '$e').toList();
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const SizedBox(height: 8),
                Center(
                  child: CircleAvatar(
                    radius: 36,
                    child: Text(
                      _initials(name.isEmpty ? email : name),
                      style: const TextStyle(fontSize: 26),
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                Center(
                  child: Text(name.isEmpty ? email : name,
                      style: Theme.of(context).textTheme.titleLarge),
                ),
                if (name.isNotEmpty && email.isNotEmpty)
                  Center(
                    child: Text(email,
                        style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                            color: Theme.of(context).colorScheme.outline)),
                  ),
                if (role.isNotEmpty) ...[
                  const SizedBox(height: 10),
                  Center(
                    child: Chip(
                      avatar: const Icon(Icons.badge_outlined, size: 18),
                      label: Text(role),
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                ],
                const SizedBox(height: 24),
                if (company.isNotEmpty) ...[
                  Text('Company',
                      style: Theme.of(context).textTheme.labelLarge),
                  const SizedBox(height: 4),
                  Card(
                    child: Column(children: [
                      _row(context, Icons.business,
                          '${company['name'] ?? '—'}',
                          subtitle: '${company['trading_name'] ?? ''}'),
                      if ('${company['registration_no'] ?? ''}'.isNotEmpty)
                        _row(context, Icons.badge_outlined,
                            'Reg ${company['registration_no']}'),
                      if ('${company['vat_no'] ?? ''}'.isNotEmpty)
                        _row(context, Icons.receipt_long_outlined,
                            'VAT ${company['vat_no']}'),
                    ]),
                  ),
                  const SizedBox(height: 16),
                ],
                if (widget.api.canManageCompany || widget.api.canInviteUsers) ...[
                  Text('Admin', style: Theme.of(context).textTheme.labelLarge),
                  const SizedBox(height: 4),
                  Card(
                    child: Column(children: [
                      if (widget.api.canManageCompany)
                        ListTile(
                          leading: const Icon(Icons.settings_outlined),
                          title: const Text('Company settings'),
                          subtitle:
                              const Text('Name, registration, VAT, address'),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () => Navigator.of(context)
                              .push(MaterialPageRoute(
                                builder: (_) =>
                                    CompanySettingsScreen(api: widget.api),
                              ))
                              .then((changed) {
                            if (changed == true) {
                              setState(() { _future = _load(); });
                            }
                          }),
                        ),
                      if (widget.api.canInviteUsers)
                        ListTile(
                          leading: const Icon(Icons.group_outlined),
                          title: const Text('Team'),
                          subtitle: const Text('Invite and manage members'),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () => Navigator.of(context).push(MaterialPageRoute(
                            builder: (_) => TeamScreen(api: widget.api),
                          )),
                        ),
                    ]),
                  ),
                  const SizedBox(height: 16),
                ],
                Text('Account', style: Theme.of(context).textTheme.labelLarge),
                const SizedBox(height: 4),
                Card(
                  child: Column(children: [
                    if ('${user['mobile'] ?? ''}'.isNotEmpty)
                      _row(context, Icons.phone_outlined, '${user['mobile']}'),
                    _row(context, Icons.dns_outlined, widget.api.origin),
                  ]),
                ),
                if (_capabilities(perms).isNotEmpty) ...[
                  const SizedBox(height: 16),
                  Text('What you can do',
                      style: Theme.of(context).textTheme.labelLarge),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final cap in _capabilities(perms))
                        Chip(
                          avatar: const Icon(Icons.check, size: 16),
                          label: Text(cap),
                          visualDensity: VisualDensity.compact,
                        ),
                    ],
                  ),
                ],
                const SizedBox(height: 28),
                OutlinedButton.icon(
                  onPressed: _confirmSignOut,
                  icon: const Icon(Icons.logout),
                  label: const Text('Sign out'),
                  style: OutlinedButton.styleFrom(
                      foregroundColor: Theme.of(context).colorScheme.error),
                ),
                const SizedBox(height: 24),
              ],
            );
          },
        ),
      ),
    );
  }

  /// Friendly labels for the permissions this user holds — turns permission
  /// codenames into plain-language capabilities. Only mapped codes are shown.
  static const _capabilityLabels = {
    'finance.view_money': 'See financials',
    'finance.manage': 'Manage finances',
    'company.manage': 'Company settings',
    'users.invite': 'Invite team',
    'billing.manage': 'Manage billing',
    'compliance.manage': 'Submit compliance',
    'compliance.override': 'Approve compliance',
    'projects.create': 'Create projects',
    'work.create': 'Create work',
    'work.assign': 'Assign work',
    'work.approve': 'Approve work',
    'quotes.create': 'Create quotes',
    'quotes.approve': 'Approve quotes',
    'invoices.approve': 'Approve invoices',
    'po.approve': 'Approve POs',
    'procurement.manage': 'Manage procurement',
    'rfq.upload': 'Manage RFQs',
    'estimating.manage': 'Manage estimates',
    'timesheet.approve': 'Approve timesheets',
    'crm.manage': 'Manage CRM',
    'ai.generate': 'Use Lulaworks AI',
  };

  List<String> _capabilities(List<String> perms) => [
        for (final p in perms)
          if (_capabilityLabels[p] != null) _capabilityLabels[p]!,
      ];

  Widget _row(BuildContext context, IconData icon, String title,
      {String? subtitle}) {
    return ListTile(
      dense: true,
      leading: Icon(icon, size: 20),
      title: Text(title),
      subtitle: (subtitle != null && subtitle.isNotEmpty) ? Text(subtitle) : null,
    );
  }

  String _initials(String s) {
    final parts = s.trim().split(RegExp(r'\s+')).where((p) => p.isNotEmpty).toList();
    if (parts.isEmpty) return '?';
    if (parts.length == 1) return parts.first.characters.first.toUpperCase();
    return (parts.first.characters.first + parts.last.characters.first).toUpperCase();
  }
}

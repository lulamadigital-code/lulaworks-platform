import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../ui/tokens.dart';
import 'billing_screen.dart';
import 'company_settings_screen.dart';
import 'help_support_screen.dart';
import 'profile_screen.dart';
import 'team_screen.dart';

/// One place for every setting — mirrors the web Settings hub. Each row opens the
/// screen that owns that setting; those screens enforce their own permissions,
/// and rows are permission-gated here so a user only sees what they can manage.
class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key, required this.api, required this.onSignOut});
  final ApiClient api;
  final Future<void> Function() onSignOut;

  @override
  Widget build(BuildContext context) {
    final canCompany = api.canManageCompany;
    final canTeam = api.canInviteUsers || api.canManageCompany;
    return Scaffold(
      appBar: AppBar(title: const Text('Settings'), scrolledUnderElevation: 1),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: LwSpace.sm),
        children: [
          _section('Workspace'),
          if (canCompany)
            _tile(context, Icons.business_outlined, 'Company profile',
                'Registered details, banking & branding',
                () => CompanySettingsScreen(api: api)),
          if (canCompany)
            _tile(context, Icons.credit_card_outlined, 'Billing & usage',
                'Plan, AI credits, seats & storage',
                () => BillingScreen(api: api)),
          if (canTeam)
            _tile(context, Icons.group_outlined, 'Users & employees',
                'Invite & manage people',
                () => TeamScreen(api: api)),
          if (!canCompany && !canTeam)
            const Padding(
              padding: EdgeInsets.fromLTRB(LwSpace.lg, LwSpace.xs, LwSpace.lg, LwSpace.md),
              child: Text('Workspace settings are managed by your company admin.',
                  style: LwType.muted),
            ),

          _section('Account'),
          _tile(context, Icons.person_outline, 'My profile',
              'Your name, photo & password',
              () => ProfileScreen(api: api, onSignOut: onSignOut)),

          _section('Support'),
          _tile(context, Icons.help_outline, 'Help & support',
              'Contact us & raise a ticket',
              () => HelpSupportScreen(api: api)),
        ],
      ),
    );
  }

  Widget _section(String title) => Padding(
        padding: const EdgeInsets.fromLTRB(LwSpace.lg, LwSpace.lg, LwSpace.lg, LwSpace.xs),
        child: Text(title.toUpperCase(), style: LwType.section),
      );

  Widget _tile(BuildContext context, IconData icon, String title, String subtitle,
      Widget Function() builder) {
    return ListTile(
      leading: Container(
        width: 38,
        height: 38,
        decoration: const BoxDecoration(color: kBrandTint, shape: BoxShape.circle),
        child: Icon(icon, size: 20, color: kBrandDark),
      ),
      title: Text(title, style: LwType.label),
      subtitle: Text(subtitle, style: LwType.caption),
      trailing: const Icon(Icons.chevron_right, size: 18, color: kMuted),
      onTap: () => Navigator.of(context)
          .push(MaterialPageRoute(builder: (_) => builder())),
    );
  }
}

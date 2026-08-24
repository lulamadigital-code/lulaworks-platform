import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../widgets/user_avatar.dart';
import 'change_password_screen.dart';
import 'company_settings_screen.dart';
import 'edit_profile_screen.dart';
import 'notifications_screen.dart';
import 'team_screen.dart';

/// The account centre. Cleanly separates PERSONAL profile (name, photo, security)
/// from COMPANY administration, and adapts to the user's permissions. Business
/// (subscription/usage) is shown only to company admins, from real backend data.
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

  void _reload() => setState(() { _future = _load(); });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Account'), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => _reload(),
        child: FutureBuilder<Map<String, dynamic>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const _ProfileSkeleton();
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 140),
                const Icon(Icons.cloud_off, size: 44, color: kMuted),
                const SizedBox(height: 12),
                const Center(child: Text('Something went wrong')),
                const SizedBox(height: 14),
                Center(
                    child: FilledButton(
                        onPressed: _reload, child: const Text('Try again'))),
              ]);
            }
            return _content(context, snap.data!);
          },
        ),
      ),
    );
  }

  Widget _content(BuildContext context, Map<String, dynamic> me) {
    final api = widget.api;
    final user = (me['user'] as Map?)?.cast<String, dynamic>() ?? const {};
    final company = (me['active_company'] as Map?)?.cast<String, dynamic>() ?? const {};
    final name = '${user['full_name'] ?? ''}'.trim();
    final email = '${user['email'] ?? ''}';
    final role = '${me['role'] ?? ''}';
    final jobTitle = '${me['job_title'] ?? ''}';

    final showCompany = api.canManageCompany || api.canInviteUsers;
    final showBusiness = api.canManageCompany && company.isNotEmpty;

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
      children: [
        // ── Header ──────────────────────────────────────────────────────────
        Row(children: [
          UserAvatar(
              url: user['avatar'] as String?,
              name: name.isEmpty ? email : name,
              radius: 32),
          const SizedBox(width: 16),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(name.isEmpty ? email : name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                      fontSize: 19, fontWeight: FontWeight.w700, color: kInk)),
              if (jobTitle.isNotEmpty || role.isNotEmpty)
                Text([jobTitle, role].where((s) => s.isNotEmpty).join(' · '),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 13, color: kMuted)),
              if ('${company['name'] ?? ''}'.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text('${company['name']}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 12.5, fontWeight: FontWeight.w600, color: kBrandDark)),
              ],
            ]),
          ),
        ]),
        const SizedBox(height: 14),
        OutlinedButton.icon(
          onPressed: () async {
            final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(
                builder: (_) => EditProfileScreen(api: api, me: me)));
            if (saved == true) _reload();
          },
          icon: const Icon(Icons.edit_outlined, size: 18),
          label: const Text('Edit profile'),
          style: OutlinedButton.styleFrom(
              foregroundColor: kBrandDark, side: const BorderSide(color: kLine)),
        ),
        const SizedBox(height: 24),

        // ── Account ─────────────────────────────────────────────────────────
        _sectionLabel('Account'),
        _group([
          _tile(Icons.person_outline, 'Personal information',
              email.isNotEmpty ? email : 'Name, mobile, photo', () async {
            final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(
                builder: (_) => EditProfileScreen(api: api, me: me)));
            if (saved == true) _reload();
          }),
          _tile(Icons.lock_outline, 'Security', 'Change your password',
              () => _push(ChangePasswordScreen(api: api))),
          _tile(Icons.notifications_none, 'Notifications', 'Alerts & mentions',
              () => _push(NotificationsScreen(api: api))),
        ]),

        // ── Company ─────────────────────────────────────────────────────────
        if (showCompany) ...[
          const SizedBox(height: 20),
          _sectionLabel('Company'),
          _group([
            if (api.canManageCompany)
              _tile(Icons.business_outlined, 'Company profile',
                  'Name, registration, VAT, address',
                  () => _push(CompanySettingsScreen(api: api))),
            if (api.canInviteUsers)
              _tile(Icons.groups_outlined, 'Users & employees',
                  'Invite and manage members',
                  () => _push(TeamScreen(api: api))),
          ]),
        ],

        // ── Business (real usage data) ──────────────────────────────────────
        if (showBusiness) ...[
          const SizedBox(height: 20),
          _sectionLabel('Business'),
          _usageCard(context, company),
        ],

        // ── Support ─────────────────────────────────────────────────────────
        const SizedBox(height: 20),
        _sectionLabel('Support'),
        _group([
          _tile(Icons.help_outline, 'Help & support', 'Contact the Lulaworks team',
              () => _open('mailto:support@lulaworks.com?subject=Lulaworks%20app%20support')),
          _tile(Icons.description_outlined, 'Terms of service', null,
              () => _open('https://www.lulaworks.com/terms/')),
          _tile(Icons.privacy_tip_outlined, 'Privacy policy', null,
              () => _open('https://www.lulaworks.com/privacy/')),
        ]),

        const SizedBox(height: 28),
        OutlinedButton.icon(
          onPressed: _confirmSignOut,
          icon: const Icon(Icons.logout, size: 18),
          label: const Text('Log out'),
          style: OutlinedButton.styleFrom(
              foregroundColor: kRed, side: const BorderSide(color: kLine)),
        ),
        const SizedBox(height: 8),
        Center(
          child: Text('Signed in as $email',
              style: const TextStyle(fontSize: 12, color: kMuted)),
        ),
      ],
    );
  }

  // ── Business / usage ──────────────────────────────────────────────────────
  Widget _usageCard(BuildContext context, Map<String, dynamic> c) {
    final sub = '${c['subscription_status'] ?? ''}';
    final credits = c['ai_credit_balance'];
    final used = (c['storage_used_bytes'] as num?)?.toDouble() ?? 0;
    final quota = (c['storage_quota_bytes'] as num?)?.toDouble() ?? 0;
    final maxUsers = c['max_users'];
    String gb(double b) => '${(b / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';

    return Container(
      decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: kLine)),
      padding: const EdgeInsets.all(16),
      child: Column(children: [
        if (sub.isNotEmpty)
          _usageRow(context, Icons.workspace_premium_outlined, 'Plan',
              sub[0].toUpperCase() + sub.substring(1)),
        if (credits != null)
          _usageRow(context, Icons.auto_awesome_outlined, 'Lulaworks AI credits',
              '$credits'),
        if (quota > 0) ...[
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                const Icon(Icons.cloud_outlined, size: 18, color: kMuted),
                const SizedBox(width: 10),
                const Expanded(
                    child: Text('Storage', style: TextStyle(fontSize: 14, color: kInk))),
                Text('${gb(used)} / ${gb(quota)}',
                    style: const TextStyle(
                        fontSize: 13, fontWeight: FontWeight.w600, color: kInk)),
              ]),
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: quota > 0 ? (used / quota).clamp(0, 1) : 0,
                  minHeight: 6,
                  backgroundColor: kBrandTint,
                  color: kBrand,
                ),
              ),
            ]),
          ),
        ],
        if (maxUsers != null)
          _usageRow(context, Icons.people_outline, 'User limit', '$maxUsers users'),
      ]),
    );
  }

  Widget _usageRow(BuildContext context, IconData icon, String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(children: [
        Icon(icon, size: 18, color: kMuted),
        const SizedBox(width: 10),
        Expanded(child: Text(label, style: const TextStyle(fontSize: 14, color: kInk))),
        Text(value,
            style: const TextStyle(
                fontSize: 13.5, fontWeight: FontWeight.w600, color: kInk)),
      ]),
    );
  }

  // ── Shared bits ───────────────────────────────────────────────────────────
  Widget _sectionLabel(String s) => Padding(
        padding: const EdgeInsets.only(left: 4, bottom: 8),
        child: Text(s.toUpperCase(),
            style: const TextStyle(
                fontSize: 11.5,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.6,
                color: kMuted)),
      );

  Widget _group(List<Widget> tiles) {
    final children = <Widget>[];
    for (var i = 0; i < tiles.length; i++) {
      if (i > 0) children.add(const Divider(height: 1, indent: 56));
      children.add(tiles[i]);
    }
    return Container(
      decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: kLine)),
      child: Column(children: children),
    );
  }

  Widget _tile(IconData icon, String title, String? subtitle, VoidCallback onTap) {
    return ListTile(
      onTap: onTap,
      leading: Container(
        width: 36,
        height: 36,
        decoration: BoxDecoration(
            color: kBrand.withOpacity(0.08), borderRadius: BorderRadius.circular(10)),
        child: Icon(icon, size: 20, color: kBrandDark),
      ),
      title: Text(title,
          style: const TextStyle(
              fontSize: 14.5, fontWeight: FontWeight.w500, color: kInk)),
      subtitle: subtitle == null
          ? null
          : Text(subtitle,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12.5, color: kMuted)),
      trailing: const Icon(Icons.chevron_right, size: 20, color: kMuted),
    );
  }

  void _push(Widget screen) =>
      Navigator.of(context).push(MaterialPageRoute(builder: (_) => screen));

  Future<void> _open(String url) async {
    final uri = Uri.parse(url);
    final ok = await canLaunchUrl(uri) &&
        await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!ok && mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text("Couldn't open $url")));
    }
  }

  Future<void> _confirmSignOut() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Log out?'),
        content: const Text('You will need to sign in again to use the app.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(
              style: FilledButton.styleFrom(backgroundColor: kRed),
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Log out')),
        ],
      ),
    );
    if (ok == true) await widget.onSignOut();
  }
}

class _ProfileSkeleton extends StatelessWidget {
  const _ProfileSkeleton();
  @override
  Widget build(BuildContext context) {
    Widget box(double w, double h, {double r = 8}) => Container(
        width: w,
        height: h,
        decoration:
            BoxDecoration(color: kLine, borderRadius: BorderRadius.circular(r)));
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 20, 20, 32),
      children: [
        Row(children: [
          const CircleAvatar(radius: 32, backgroundColor: kLine),
          const SizedBox(width: 16),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              box(160, 18),
              const SizedBox(height: 8),
              box(110, 13),
            ]),
          ),
        ]),
        const SizedBox(height: 20),
        box(double.infinity, 44, r: 10),
        const SizedBox(height: 24),
        box(70, 12),
        const SizedBox(height: 10),
        box(double.infinity, 150, r: 14),
        const SizedBox(height: 20),
        box(70, 12),
        const SizedBox(height: 10),
        box(double.infinity, 100, r: 14),
      ],
    );
  }
}

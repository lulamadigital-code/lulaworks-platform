import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/api_client.dart';
import '../theme.dart';
import 'customer_detail_screen.dart';

/// Contact profile — the relationship view for one person, matching the web
/// Contact Profile: who they are, how to reach them, the overview tallies
/// (calls / meetings / open deals / active jobs), and a chronological feed.
class ContactDetailScreen extends StatefulWidget {
  const ContactDetailScreen({super.key, required this.api, required this.contactId});
  final ApiClient api;
  final String contactId;

  @override
  State<ContactDetailScreen> createState() => _ContactDetailScreenState();
}

class _ContactDetailScreenState extends State<ContactDetailScreen> {
  late Future<Map<String, dynamic>> _future = _load();

  Future<Map<String, dynamic>> _load() async {
    final body = await widget.api.get('/customer-contacts/${widget.contactId}/profile/');
    return (body as Map).cast<String, dynamic>();
  }

  Future<void> _refresh() async {
    setState(() => _future = _load());
    await _future;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Contact'), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: _refresh,
        child: FutureBuilder<Map<String, dynamic>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator(color: kBrand));
            }
            if (snap.hasError) {
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('Could not load this contact.', style: TextStyle(color: kMuted))),
              ]);
            }
            final d = snap.data!;
            final c = (d['contact'] as Map?)?.cast<String, dynamic>() ?? const {};
            final ov = (d['overview'] as Map?)?.cast<String, dynamic>() ?? const {};
            final roles = (d['roles'] as List?)?.cast<String>() ?? const [];
            final resp = (d['responsibilities'] as List?)?.cast<String>() ?? const [];
            final opps = (d['open_opportunities'] as List?)
                    ?.map((e) => (e as Map).cast<String, dynamic>())
                    .toList() ??
                const [];
            final feed = (d['feed'] as List?)
                    ?.map((e) => (e as Map).cast<String, dynamic>())
                    .toList() ??
                const [];
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _header(c, '${d['customer_name'] ?? ''}'),
                if ('${d['customer_id'] ?? ''}'.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  _customerLink('${d['customer_name'] ?? 'Customer'}', '${d['customer_id']}'),
                ],
                const SizedBox(height: 16),
                _reachRow(c),
                const SizedBox(height: 16),
                _overview(ov),
                if (roles.isNotEmpty || resp.isNotEmpty) ...[
                  const SizedBox(height: 16),
                  _chipsCard('Roles & responsibilities', [...roles, ...resp]),
                ],
                if (opps.isNotEmpty) ...[
                  const SizedBox(height: 16),
                  _oppsCard(opps),
                ],
                if ('${c['notes'] ?? ''}'.trim().isNotEmpty) ...[
                  const SizedBox(height: 16),
                  _notesCard('${c['notes']}'),
                ],
                const SizedBox(height: 16),
                _feedCard(feed),
                const SizedBox(height: 24),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _header(Map<String, dynamic> c, String company) {
    final name = '${c['full_name'] ?? ''}'.trim();
    final role = '${c['job_title'] ?? ''}'.trim();
    final isPrimary = c['is_primary'] == true;
    return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Container(
        width: 58,
        height: 58,
        alignment: Alignment.center,
        decoration: BoxDecoration(
            color: kBrand.withOpacity(0.10), borderRadius: BorderRadius.circular(16)),
        child: Text(name.isEmpty ? '?' : name.substring(0, 1).toUpperCase(),
            style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w700, color: kBrandDark)),
      ),
      const SizedBox(width: 14),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Flexible(
              child: Text(name.isEmpty ? 'Unnamed' : name,
                  style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700, color: kInk)),
            ),
            if (isPrimary) ...[
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                decoration: BoxDecoration(color: kBrandTint, borderRadius: BorderRadius.circular(7)),
                child: const Text('Primary',
                    style: TextStyle(fontSize: 10.5, color: kBrandDark, fontWeight: FontWeight.w700)),
              ),
            ],
          ]),
          const SizedBox(height: 4),
          Text([if (role.isNotEmpty) role, if (company.isNotEmpty) company].join(' · '),
              style: const TextStyle(fontSize: 13.5, color: kMuted)),
        ]),
      ),
    ]);
  }

  Widget _customerLink(String name, String customerId) {
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(12),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => CustomerDetailScreen(api: widget.api, customerId: customerId))),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 12),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: kLine),
          ),
          child: Row(children: [
            const Icon(Icons.business_outlined, size: 20, color: kBrandDark),
            const SizedBox(width: 10),
            Expanded(
              child: Text(name,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: kInk)),
            ),
            const Icon(Icons.chevron_right, color: kMuted, size: 20),
          ]),
        ),
      ),
    );
  }

  Widget _reachRow(Map<String, dynamic> c) {
    final email = '${c['email'] ?? ''}'.trim();
    final mobile = '${c['mobile'] ?? c['telephone'] ?? ''}'.trim();
    final whatsapp = '${c['whatsapp'] ?? ''}'.trim();
    final btns = <Widget>[
      if (email.isNotEmpty) _reachBtn(Icons.email_outlined, 'Email', () => _launch('mailto:$email')),
      if (mobile.isNotEmpty) _reachBtn(Icons.call_outlined, 'Call', () => _launch('tel:$mobile')),
      if (whatsapp.isNotEmpty)
        _reachBtn(Icons.chat_outlined, 'WhatsApp',
            () => _launch('https://wa.me/${whatsapp.replaceAll(RegExp(r'[^0-9]'), '')}')),
    ];
    if (btns.isEmpty) return const SizedBox.shrink();
    return Row(children: [
      for (var i = 0; i < btns.length; i++) ...[
        if (i > 0) const SizedBox(width: 10),
        Expanded(child: btns[i]),
      ],
    ]);
  }

  Widget _reachBtn(IconData icon, String label, VoidCallback onTap) {
    return OutlinedButton.icon(
      onPressed: onTap,
      style: OutlinedButton.styleFrom(
        foregroundColor: kBrandDark,
        side: const BorderSide(color: kLine),
        padding: const EdgeInsets.symmetric(vertical: 12),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
      icon: Icon(icon, size: 18),
      label: Text(label),
    );
  }

  Widget _overview(Map<String, dynamic> ov) {
    final last = _dateLabel('${ov['last_contact'] ?? ''}');
    final next = _dateLabel('${ov['next_follow_up'] ?? ''}');
    return _card('Overview', child: Column(children: [
      Row(children: [
        _stat('Calls', '${ov['calls'] ?? 0}'),
        _stat('Meetings', '${ov['meetings'] ?? 0}'),
        _stat('Emails', '${ov['emails'] ?? 0}'),
        _stat('WhatsApp', '${ov['whatsapp'] ?? 0}'),
      ]),
      const SizedBox(height: 12),
      Row(children: [
        _stat('Open deals', '${ov['open_opportunities'] ?? 0}'),
        _stat('Active jobs', '${ov['active_jobs'] ?? 0}'),
        _stat('Last contact', last.isEmpty ? '—' : last, wide: true),
      ]),
      if (next.isNotEmpty) ...[
        const SizedBox(height: 12),
        Align(
          alignment: Alignment.centerLeft,
          child: Text('Next follow-up: $next',
              style: const TextStyle(fontSize: 12.5, color: kBrandDark, fontWeight: FontWeight.w600)),
        ),
      ],
    ]));
  }

  Widget _stat(String label, String value, {bool wide = false}) {
    return Expanded(
      flex: wide ? 2 : 1,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(value, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700, color: kInk)),
        const SizedBox(height: 2),
        Text(label, style: const TextStyle(fontSize: 11.5, color: kMuted)),
      ]),
    );
  }

  Widget _chipsCard(String title, List<String> items) {
    return _card(title, child: Wrap(spacing: 8, runSpacing: 8, children: [
      for (final it in items)
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
          decoration: BoxDecoration(color: kBrandTint, borderRadius: BorderRadius.circular(20)),
          child: Text(it, style: const TextStyle(fontSize: 12, color: kBrandDark, fontWeight: FontWeight.w600)),
        ),
    ]));
  }

  Widget _oppsCard(List<Map<String, dynamic>> opps) {
    return _card('Open opportunities', child: Column(children: [
      for (var i = 0; i < opps.length; i++) ...[
        if (i > 0) const Divider(height: 14, color: kLine),
        Row(children: [
          const Icon(Icons.filter_alt_outlined, size: 18, color: kBrandDark),
          const SizedBox(width: 10),
          Expanded(
            child: Text('${opps[i]['title']}',
                style: const TextStyle(fontSize: 13.5, color: kInk, fontWeight: FontWeight.w600)),
          ),
          Text('${opps[i]['stage'] ?? ''}', style: const TextStyle(fontSize: 12, color: kMuted)),
        ]),
      ],
    ]));
  }

  Widget _notesCard(String notes) {
    return _card('Notes',
        child: Text(notes, style: const TextStyle(fontSize: 13.5, color: kInk, height: 1.4)));
  }

  Widget _feedCard(List<Map<String, dynamic>> feed) {
    if (feed.isEmpty) {
      return _card('Activity',
          child: const Text('Nothing logged with this person yet.',
              style: TextStyle(fontSize: 13, color: kMuted)));
    }
    return _card('Activity', child: Column(children: [
      for (var i = 0; i < feed.length; i++) ...[
        if (i > 0) const Divider(height: 16, color: kLine),
        _feedRow(feed[i]),
      ],
    ]));
  }

  Widget _feedRow(Map<String, dynamic> e) {
    final when = _dateLabel('${e['when'] ?? ''}');
    final kind = '${e['kind'] ?? ''}';
    final title = '${e['title'] ?? ''}';
    final detail = '${e['detail'] ?? ''}'.trim();
    return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Container(
        margin: const EdgeInsets.only(top: 4),
        width: 8,
        height: 8,
        decoration: const BoxDecoration(color: kBrand, shape: BoxShape.circle),
      ),
      const SizedBox(width: 12),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(
              child: Text(title.isEmpty ? kind : title,
                  style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, color: kInk)),
            ),
            if (when.isNotEmpty)
              Text(when, style: const TextStyle(fontSize: 11.5, color: kMuted)),
          ]),
          if (kind.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 1),
              child: Text(kind, style: const TextStyle(fontSize: 11.5, color: kBrandDark)),
            ),
          if (detail.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 3),
              child: Text(detail, style: const TextStyle(fontSize: 12.5, color: kMuted, height: 1.35)),
            ),
        ]),
      ),
    ]);
  }

  Widget _card(String title, {required Widget child}) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: kLine),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title,
            style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: kMuted, letterSpacing: 0.3)),
        const SizedBox(height: 12),
        child,
      ]),
    );
  }

  String _dateLabel(String iso) {
    final d = DateTime.tryParse(iso);
    if (d == null) return '';
    final l = d.toLocal();
    return '${l.day}/${l.month}/${l.year}';
  }

  Future<void> _launch(String uri) async {
    final messenger = ScaffoldMessenger.of(context);
    if (!await launchUrl(Uri.parse(uri), mode: LaunchMode.externalApplication)) {
      messenger.showSnackBar(const SnackBar(content: Text('Could not open that app.')));
    }
  }
}

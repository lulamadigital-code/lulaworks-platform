import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import 'activities_screen.dart';
import 'contacts_screen.dart';
import 'customers_screen.dart';
import 'leads_screen.dart';
import 'pipeline_screen.dart';

/// CRM home — one hub tying Customers, Leads, Pipeline and Activities together,
/// with live open-counts, plus a preview of the people we work with. Parity
/// with the web CRM hub.
class CrmHomeScreen extends StatefulWidget {
  const CrmHomeScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<CrmHomeScreen> createState() => _CrmHomeScreenState();
}

class _CrmHomeScreenState extends State<CrmHomeScreen> {
  late Future<_HubData> _future = _load();

  Future<int> _count(String path) async {
    try {
      final body = await widget.api.get(path);
      if (body is Map && body['count'] is int) return body['count'] as int;
      if (body is Map && body['results'] is List) return (body['results'] as List).length;
      if (body is List) return body.length;
    } catch (_) {/* a count is a nicety — never block the hub */}
    return 0;
  }

  Future<_HubData> _load() async {
    final r = await Future.wait([
      _count('/leads/?status=open'),
      _count('/opportunities/?status=open'),
      _count('/activities/?mine=1&status=open'),
    ]);
    // People preview — show the first several; the full, searchable list lives
    // on the People screen. Never let this block the hub.
    List<Map<String, dynamic>> people = const [];
    int peopleTotal = 0;
    try {
      final body = await widget.api.get('/customer-contacts/');
      people = pageResults(body).cast<Map<String, dynamic>>();
      if (body is Map && body['count'] is int) {
        peopleTotal = body['count'] as int;
      } else {
        peopleTotal = people.length;
      }
    } catch (_) {/* ignore */}
    return _HubData(
      leads: r[0],
      deals: r[1],
      activities: r[2],
      people: people,
      peopleTotal: peopleTotal,
    );
  }

  Future<void> _refresh() async {
    setState(() => _future = _load());
    await _future;
  }

  void _push(Widget s) async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => s));
    _refresh(); // counts may have changed
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('CRM'), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: _refresh,
        child: FutureBuilder<_HubData>(
          future: _future,
          builder: (context, snap) {
            final d = snap.data ?? _HubData.empty();
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _peopleSection(d),
                _tile(Icons.contacts_outlined, 'Customers',
                    'Companies, contacts & activity', null,
                    () => _push(CustomersScreen(api: widget.api))),
                _tile(Icons.person_search_outlined, 'Leads',
                    'Prospects to convert or track', d.leads,
                    () => _push(LeadsScreen(api: widget.api))),
                _tile(Icons.filter_alt_outlined, 'Pipeline',
                    'Open deals by stage', d.deals,
                    () => _push(PipelineScreen(api: widget.api))),
                _tile(Icons.event_available_outlined, 'Activities',
                    'Your calls, meetings & follow-ups', d.activities,
                    () => _push(ActivitiesScreen(api: widget.api))),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _peopleSection(_HubData d) {
    if (d.people.isEmpty) return const SizedBox.shrink();
    final preview = d.people.take(8).toList();
    final hasMore = d.peopleTotal > preview.length;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const SizedBox(height: 8),
      Padding(
        padding: const EdgeInsets.fromLTRB(2, 6, 2, 8),
        child: Row(children: [
          const Text('People we work with',
              style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: kInk)),
          const SizedBox(width: 6),
          if (d.peopleTotal > 0)
            Text('· ${d.peopleTotal}', style: const TextStyle(fontSize: 13, color: kMuted)),
        ]),
      ),
      Container(
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: kLine),
        ),
        child: Column(children: [
          for (var i = 0; i < preview.length; i++) ...[
            if (i > 0) const Divider(height: 1, color: kLine),
            _personRow(preview[i]),
          ],
        ]),
      ),
      const SizedBox(height: 10),
      SizedBox(
        width: double.infinity,
        child: OutlinedButton.icon(
          onPressed: () => _push(ContactsScreen(api: widget.api)),
          style: OutlinedButton.styleFrom(
            foregroundColor: kBrandDark,
            side: const BorderSide(color: kLine),
            padding: const EdgeInsets.symmetric(vertical: 12),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          ),
          icon: const Icon(Icons.search, size: 18),
          label: Text(hasMore ? 'View all ${d.peopleTotal} & search' : 'View all & search'),
        ),
      ),
      const SizedBox(height: 8),
    ]);
  }

  Widget _personRow(Map<String, dynamic> p) {
    final name = '${p['full_name'] ?? ''}'.trim();
    final role = '${p['job_title'] ?? ''}'.trim();
    final company = '${p['customer_name'] ?? ''}'.trim();
    final sub = [role.isEmpty ? 'Contact' : role, if (company.isNotEmpty) company].join(' · ');
    final isPrimary = p['is_primary'] == true;
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      leading: Container(
        width: 38,
        height: 38,
        alignment: Alignment.center,
        decoration: BoxDecoration(
            color: kBrand.withOpacity(0.10), borderRadius: BorderRadius.circular(11)),
        child: Text(name.isEmpty ? '?' : name.substring(0, 1).toUpperCase(),
            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: kBrandDark)),
      ),
      title: Row(children: [
        Flexible(
          child: Text(name.isEmpty ? 'Unnamed' : name,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 14.5, fontWeight: FontWeight.w600, color: kInk)),
        ),
        if (isPrimary) ...[
          const SizedBox(width: 6),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
            decoration: BoxDecoration(color: kBrandTint, borderRadius: BorderRadius.circular(6)),
            child: const Text('Primary',
                style: TextStyle(fontSize: 9, color: kBrandDark, fontWeight: FontWeight.w700)),
          ),
        ],
      ]),
      subtitle: Text(sub, overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontSize: 12, color: kMuted)),
      onTap: () => _push(ContactsScreen(api: widget.api)),
    );
  }

  Widget _tile(IconData icon, String title, String subtitle, int? count, VoidCallback onTap) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: kLine),
      ),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        leading: Container(
          width: 44,
          height: 44,
          decoration: BoxDecoration(
              color: kBrand.withOpacity(0.08), borderRadius: BorderRadius.circular(12)),
          child: Icon(icon, color: kBrandDark, size: 22),
        ),
        title: Text(title,
            style: const TextStyle(fontSize: 15.5, fontWeight: FontWeight.w600, color: kInk)),
        subtitle: Text(subtitle, style: const TextStyle(fontSize: 12.5, color: kMuted)),
        trailing: Row(mainAxisSize: MainAxisSize.min, children: [
          if (count != null && count > 0)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
              decoration: BoxDecoration(color: kBrandTint, borderRadius: BorderRadius.circular(20)),
              child: Text('$count',
                  style: const TextStyle(fontSize: 12.5, color: kBrandDark, fontWeight: FontWeight.w700)),
            ),
          const SizedBox(width: 6),
          const Icon(Icons.chevron_right, color: kMuted),
        ]),
        onTap: onTap,
      ),
    );
  }
}

class _HubData {
  const _HubData({
    required this.leads,
    required this.deals,
    required this.activities,
    required this.people,
    required this.peopleTotal,
  });
  factory _HubData.empty() =>
      const _HubData(leads: 0, deals: 0, activities: 0, people: [], peopleTotal: 0);
  final int leads;
  final int deals;
  final int activities;
  final List<Map<String, dynamic>> people;
  final int peopleTotal;
}

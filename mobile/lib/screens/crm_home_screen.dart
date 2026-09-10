import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import 'activities_screen.dart';
import 'customers_screen.dart';
import 'leads_screen.dart';
import 'pipeline_screen.dart';

/// CRM home — one hub tying Customers, Leads, Pipeline and Activities together,
/// with live open-counts. Parity with the web CRM hub.
class CrmHomeScreen extends StatefulWidget {
  const CrmHomeScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<CrmHomeScreen> createState() => _CrmHomeScreenState();
}

class _CrmHomeScreenState extends State<CrmHomeScreen> {
  late Future<Map<String, int>> _future = _load();

  Future<int> _count(String path) async {
    try {
      final body = await widget.api.get(path);
      if (body is Map && body['count'] is int) return body['count'] as int;
      if (body is Map && body['results'] is List) return (body['results'] as List).length;
      if (body is List) return body.length;
    } catch (_) {/* a count is a nicety — never block the hub */}
    return 0;
  }

  Future<Map<String, int>> _load() async {
    final r = await Future.wait([
      _count('/leads/?status=open'),
      _count('/opportunities/?status=open'),
      _count('/activities/?mine=1&status=open'),
    ]);
    return {'leads': r[0], 'deals': r[1], 'activities': r[2]};
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
        child: FutureBuilder<Map<String, int>>(
          future: _future,
          builder: (context, snap) {
            final c = snap.data ?? const {'leads': 0, 'deals': 0, 'activities': 0};
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _tile(Icons.contacts_outlined, 'Customers',
                    'Companies, contacts & activity', null,
                    () => _push(CustomersScreen(api: widget.api))),
                _tile(Icons.person_search_outlined, 'Leads',
                    'Prospects to convert or track', c['leads'],
                    () => _push(LeadsScreen(api: widget.api))),
                _tile(Icons.filter_alt_outlined, 'Pipeline',
                    'Open deals by stage', c['deals'],
                    () => _push(PipelineScreen(api: widget.api))),
                _tile(Icons.event_available_outlined, 'Activities',
                    'Your calls, meetings & follow-ups', c['activities'],
                    () => _push(ActivitiesScreen(api: widget.api))),
              ],
            );
          },
        ),
      ),
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

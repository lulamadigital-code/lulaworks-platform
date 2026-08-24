import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import 'crm_log_screen.dart';
import 'customer_form_screen.dart';

/// Customer detail — identity + overview, the merged CRM timeline, and the
/// people, split across tabs so the first screen isn't overloaded (§8). The Log
/// button records a call/meeting/note/follow-up against this customer.
class CustomerDetailScreen extends StatefulWidget {
  const CustomerDetailScreen({super.key, required this.api, required this.customerId});
  final ApiClient api;
  final String customerId;

  @override
  State<CustomerDetailScreen> createState() => _CustomerDetailScreenState();
}

class _CustomerDetailScreenState extends State<CustomerDetailScreen> {
  late Future<_CustomerDetail> _future = _load();

  Future<_CustomerDetail> _load() async {
    final id = widget.customerId;
    final results = await Future.wait([
      widget.api.get('/customers/$id/'),
      widget.api.get('/customers/$id/overview/').catchError((_) => null),
      widget.api.get('/customers/$id/contacts/').catchError((_) => null),
      widget.api.get('/customers/$id/timeline/').catchError((_) => null),
    ]);
    return _CustomerDetail(
      customer: (results[0] as Map).cast<String, dynamic>(),
      overview: results[1] is Map ? (results[1] as Map).cast<String, dynamic>() : const {},
      contacts: results[2] is List ? (results[2] as List).cast<Map<String, dynamic>>() : const [],
      timeline: results[3] is List ? (results[3] as List).cast<Map<String, dynamic>>() : const [],
    );
  }

  void _reload() => setState(() { _future = _load(); });

  Future<void> _edit(Map<String, dynamic> customer) async {
    final saved = await Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => CustomerFormScreen(api: widget.api, existing: customer),
    ));
    if (saved != null) _reload();
  }

  Future<void> _log() async {
    final ok = await Navigator.of(context).push<bool>(MaterialPageRoute(
      builder: (_) => CrmLogScreen(api: widget.api, customerId: widget.customerId),
    ));
    if (ok == true) _reload();
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<_CustomerDetail>(
      future: _future,
      builder: (context, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return const Scaffold(body: Center(child: CircularProgressIndicator()));
        }
        if (snap.hasError) {
          return Scaffold(
            appBar: AppBar(),
            body: Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
          );
        }
        final d = snap.data!;
        final cst = d.customer;
        return DefaultTabController(
          length: 3,
          child: Scaffold(
            appBar: AppBar(
              title: Text('${cst['code'] ?? ''}'),
              actions: [
                if (widget.api.canManageCustomers)
                  IconButton(
                      icon: const Icon(Icons.edit_outlined),
                      onPressed: () => _edit(cst)),
              ],
              bottom: const TabBar(tabs: [
                Tab(text: 'Overview'),
                Tab(text: 'Timeline'),
                Tab(text: 'People'),
              ]),
            ),
            floatingActionButton: widget.api.canSeeCustomers
                ? FloatingActionButton.extended(
                    onPressed: _log,
                    icon: const Icon(Icons.add_comment_outlined),
                    label: const Text('Log'))
                : null,
            body: TabBarView(children: [
              _overviewTab(context, d),
              _timelineTab(context, d.timeline),
              _peopleTab(context, d.contacts),
            ]),
          ),
        );
      },
    );
  }

  Widget _overviewTab(BuildContext context, _CustomerDetail d) {
    final cst = d.customer;
    return ListView(padding: const EdgeInsets.all(16), children: [
      Text('${cst['name'] ?? ''}', style: Theme.of(context).textTheme.headlineSmall),
      if ('${cst['trading_name'] ?? ''}'.isNotEmpty)
        Text('t/a ${cst['trading_name']}',
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                color: Theme.of(context).colorScheme.outline)),
      const SizedBox(height: 8),
      Wrap(spacing: 8, runSpacing: 8, children: [
        _StatusChip(status: '${cst['status'] ?? ''}'),
        if ('${cst['customer_type'] ?? ''}'.isNotEmpty)
          Chip(label: Text('${cst['customer_type']}'), visualDensity: VisualDensity.compact),
      ]),
      const SizedBox(height: 16),
      _overviewGrid(context, d.overview),
      const SizedBox(height: 16),
      _contactMethods(context, cst),
    ]);
  }

  Widget _timelineTab(BuildContext context, List<Map<String, dynamic>> events) {
    if (events.isEmpty) {
      return ListView(children: const [
        SizedBox(height: 120),
        Center(child: Text('No history yet — tap Log to add the first entry.')),
      ]);
    }
    return ListView.separated(
      padding: const EdgeInsets.all(16),
      itemCount: events.length,
      separatorBuilder: (_, __) => const SizedBox(height: 4),
      itemBuilder: (context, i) => _TimelineTile(event: events[i]),
    );
  }

  Widget _peopleTab(BuildContext context, List<Map<String, dynamic>> contacts) {
    if (contacts.isEmpty) {
      return ListView(children: const [
        SizedBox(height: 120),
        Center(child: Text('No contacts recorded yet.')),
      ]);
    }
    return ListView(
      children: [for (final c in contacts) _ContactTile(contact: c)],
    );
  }

  Widget _overviewGrid(BuildContext context, Map<String, dynamic> o) {
    if (o.isEmpty) return const SizedBox.shrink();
    final items = <(String, String)>[
      ('Contacts', '${o['contacts'] ?? 0}'),
      ('Quotations', '${o['quotations'] ?? 0}'),
      ('Projects', '${o['open_projects'] ?? o['projects'] ?? 0}'),
      ('Sites', '${o['sites'] ?? 0}'),
    ];
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 8),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceAround,
          children: [
            for (final it in items)
              Column(children: [
                Text(it.$2,
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(
                        fontWeight: FontWeight.bold)),
                Text(it.$1, style: Theme.of(context).textTheme.bodySmall),
              ]),
          ],
        ),
      ),
    );
  }

  Widget _contactMethods(BuildContext context, Map<String, dynamic> cst) {
    final rows = <(IconData, String)>[
      if ('${cst['email'] ?? ''}'.isNotEmpty) (Icons.email_outlined, '${cst['email']}'),
      if ('${cst['telephone'] ?? ''}'.isNotEmpty) (Icons.phone_outlined, '${cst['telephone']}'),
      if ('${cst['mobile'] ?? ''}'.isNotEmpty) (Icons.smartphone_outlined, '${cst['mobile']}'),
      if ('${cst['city'] ?? ''}'.isNotEmpty)
        (Icons.location_on_outlined,
            [cst['city'], cst['province']].where((s) => '$s'.isNotEmpty).join(', ')),
      if ('${cst['vat_no'] ?? ''}'.isNotEmpty) (Icons.receipt_long_outlined, 'VAT ${cst['vat_no']}'),
    ];
    if (rows.isEmpty) return const SizedBox.shrink();
    return Card(
      child: Column(children: [
        for (final r in rows)
          ListTile(dense: true, leading: Icon(r.$1, size: 20), title: Text(r.$2)),
      ]),
    );
  }
}

class _TimelineTile extends StatelessWidget {
  const _TimelineTile({required this.event});
  final Map<String, dynamic> event;

  @override
  Widget build(BuildContext context) {
    final when = DateTime.tryParse('${event['when']}');
    final amount = '${event['amount'] ?? ''}';
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: Text('${event['icon'] ?? '•'}', style: const TextStyle(fontSize: 22)),
      title: Text('${event['title'] ?? ''}'),
      subtitle: Text([
        if ('${event['kind'] ?? ''}'.isNotEmpty) '${event['kind']}',
        if (when != null) '${when.day}/${when.month}/${when.year}',
        if ('${event['detail'] ?? ''}'.isNotEmpty) '${event['detail']}',
      ].join(' · '), maxLines: 2, overflow: TextOverflow.ellipsis),
      trailing: amount.isNotEmpty
          ? Text(amount, style: const TextStyle(fontWeight: FontWeight.w600))
          : null,
    );
  }
}

class _ContactTile extends StatelessWidget {
  const _ContactTile({required this.contact});
  final Map<String, dynamic> contact;

  @override
  Widget build(BuildContext context) {
    final primary = contact['is_primary'] == true;
    return ListTile(
      leading: CircleAvatar(child: Text(_initials('${contact['full_name']}'))),
      title: Row(children: [
        Flexible(child: Text('${contact['full_name']}')),
        if (primary) ...[
          const SizedBox(width: 6),
          const Icon(Icons.star, size: 15, color: Colors.amber),
        ],
      ]),
      subtitle: Text([
        if ('${contact['job_title'] ?? ''}'.isNotEmpty) '${contact['job_title']}',
        if ('${contact['reach'] ?? ''}'.isNotEmpty) '${contact['reach']}',
      ].join(' · ')),
    );
  }

  String _initials(String s) {
    final p = s.trim().split(RegExp(r'\s+')).where((x) => x.isNotEmpty).toList();
    if (p.isEmpty) return '?';
    if (p.length == 1) return p.first.characters.first.toUpperCase();
    return (p.first.characters.first + p.last.characters.first).toUpperCase();
  }
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.status});
  final String status;
  @override
  Widget build(BuildContext context) {
    final (Color c, String label) = switch (status) {
      'active' => (kGreen, 'Active'),
      'prospect' => (kInfo, 'Prospect'),
      'on_hold' => (kOrange, 'On hold'),
      'dormant' => (kMuted, 'Dormant'),
      'blacklisted' => (kRed, 'Blacklisted'),
      _ => (kMuted, status),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(
          color: c.withOpacity(0.13), borderRadius: BorderRadius.circular(8)),
      child: Text(label,
          style: TextStyle(color: c, fontSize: 11.5, fontWeight: FontWeight.w600)),
    );
  }
}

class _CustomerDetail {
  _CustomerDetail({
    required this.customer,
    required this.overview,
    required this.contacts,
    required this.timeline,
  });
  final Map<String, dynamic> customer;
  final Map<String, dynamic> overview;
  final List<Map<String, dynamic>> contacts;
  final List<Map<String, dynamic>> timeline;
}

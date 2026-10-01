import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';

/// Billing & usage — read-only on mobile. Shows the company's plan, status,
/// renewal, AI credits, seats and storage from GET /billing/summary/ (gated on
/// company.manage server-side). Plan changes and payments stay on the web
/// (PayFast) / contact-sales for Enterprise — there is nothing here that charges.
class BillingScreen extends StatefulWidget {
  const BillingScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<BillingScreen> createState() => _BillingScreenState();
}

class _BillingScreenState extends State<BillingScreen> {
  late Future<Map<String, dynamic>> _future = _load();

  Future<Map<String, dynamic>> _load() async =>
      ((await widget.api.getCached('/billing/summary/')).data as Map)
          .cast<String, dynamic>();

  static String _bytes(num? b) {
    final v = (b ?? 0).toDouble();
    if (v >= 1 << 30) return '${(v / (1 << 30)).toStringAsFixed(1)} GB';
    if (v >= 1 << 20) return '${(v / (1 << 20)).toStringAsFixed(0)} MB';
    if (v >= 1 << 10) return '${(v / (1 << 10)).toStringAsFixed(0)} KB';
    return '${v.toStringAsFixed(0)} B';
  }

  static const _statusLabel = {
    'trial': 'Trial',
    'active': 'Active',
    'past_due': 'Past due',
    'cancelled': 'Cancelled',
    'suspended': 'Suspended',
  };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Billing & usage'), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        onRefresh: () async => setState(() => _future = _load()),
        child: FutureBuilder<Map<String, dynamic>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              final forbidden =
                  snap.error is ApiException && (snap.error as ApiException).isForbidden;
              return ListView(children: [
                const SizedBox(height: 100),
                Center(
                    child: Padding(
                  padding: const EdgeInsets.all(32),
                  child: Text(
                      forbidden
                          ? 'Billing is available to company admins.'
                          : '${snap.error}',
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: kMuted)),
                )),
              ]);
            }
            return _content(context, snap.data ?? const {});
          },
        ),
      ),
    );
  }

  Widget _content(BuildContext context, Map<String, dynamic> d) {
    final plan = (d['plan'] as Map?)?.cast<String, dynamic>();
    final credits = (d['credits'] as Map?)?.cast<String, dynamic>() ?? const {};
    final seats = (d['seats'] as Map?)?.cast<String, dynamic>() ?? const {};
    final storage = (d['storage'] as Map?)?.cast<String, dynamic>() ?? const {};
    final status = '${d['status'] ?? ''}';
    final cur = '${d['currency'] ?? 'ZAR'}';
    return ListView(padding: const EdgeInsets.all(16), children: [
      // Plan card
      _card(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(
            child: Text(plan?['name'] ?? 'No active plan',
                style: const TextStyle(
                    fontSize: 20, fontWeight: FontWeight.w700, color: kInk)),
          ),
          _statusPill(status),
        ]),
        if (plan != null) ...[
          const SizedBox(height: 4),
          Text('$cur ${plan['price']}'
              '${d['billing_cycle'] != null ? ' / ${d['billing_cycle']}' : ''}',
              style: const TextStyle(fontSize: 14, color: kMuted)),
        ],
        if (d['current_period_end'] != null) ...[
          const SizedBox(height: 8),
          Text(
              '${d['cancel_at_period_end'] == true ? 'Ends' : 'Renews'} on ${d['current_period_end']}',
              style: const TextStyle(fontSize: 12.5, color: kMuted)),
        ],
      ])),

      // AI credits
      _meterCard(
        'AI credits',
        Icons.auto_awesome,
        valueLabel: '${credits['balance'] ?? '0'}'
            '${credits['monthly'] != null ? ' left · ${credits['monthly']}/mo' : ''}',
        used: _toNum(credits['monthly']) == null
            ? null
            : (_toNum(credits['monthly'])! - _toNum(credits['balance'])!)
                .clamp(0, _toNum(credits['monthly'])!),
        total: _toNum(credits['monthly']),
      ),

      // Seats
      _meterCard(
        'Team seats',
        Icons.group_outlined,
        valueLabel: '${seats['used'] ?? 0} of ${seats['included'] ?? '—'} used',
        used: _toNum(seats['used']),
        total: _toNum(seats['included']),
      ),

      // Storage
      _meterCard(
        'Storage',
        Icons.folder_outlined,
        valueLabel:
            '${_bytes(_toNum(storage['used_bytes']))} of ${_bytes(_toNum(storage['quota_bytes']))}',
        used: _toNum(storage['used_bytes']),
        total: _toNum(storage['quota_bytes']),
      ),

      if (plan?['features'] is List && (plan!['features'] as List).isNotEmpty) ...[
        const SizedBox(height: 4),
        _card(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text("What's included",
              style: TextStyle(
                  fontSize: 11, fontWeight: FontWeight.w700,
                  letterSpacing: .5, color: kMuted)),
          const SizedBox(height: 8),
          for (final f in (plan['features'] as List))
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 3),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Icon(Icons.check, size: 16, color: kGreen),
                const SizedBox(width: 8),
                Expanded(child: Text('$f', style: const TextStyle(fontSize: 13.5))),
              ]),
            ),
        ])),
      ],

      const SizedBox(height: 4),
      Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
            color: kBrandTint, borderRadius: BorderRadius.circular(12)),
        child: const Row(children: [
          Icon(Icons.info_outline, size: 18, color: kBrandDark),
          SizedBox(width: 10),
          Expanded(
            child: Text(
              'To change your plan or payment method, open Lulaworks on the web. '
              'For Enterprise, contact sales.',
              style: TextStyle(fontSize: 12.5, color: kInk),
            ),
          ),
        ]),
      ),
      const SizedBox(height: 24),
    ]);
  }

  static num? _toNum(dynamic v) {
    if (v == null) return null;
    if (v is num) return v;
    return num.tryParse('$v');
  }

  Widget _card({required Widget child}) => Container(
        margin: const EdgeInsets.only(bottom: 16),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: kLine)),
        child: child,
      );

  Widget _meterCard(String title, IconData icon,
      {required String valueLabel, num? used, num? total}) {
    final showBar = used != null && total != null && total > 0;
    final frac = showBar ? (used / total).clamp(0.0, 1.0).toDouble() : 0.0;
    final over = showBar && used > total;
    return _card(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(icon, size: 18, color: kBrand),
          const SizedBox(width: 8),
          Text(title,
              style: const TextStyle(fontWeight: FontWeight.w600, color: kInk)),
          const Spacer(),
          Text(valueLabel, style: const TextStyle(fontSize: 12.5, color: kMuted)),
        ]),
        if (showBar) ...[
          const SizedBox(height: 10),
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: LinearProgressIndicator(
              value: frac,
              minHeight: 7,
              backgroundColor: kLine,
              color: over ? kRed : kBrand,
            ),
          ),
        ],
      ]),
    );
  }

  Widget _statusPill(String status) {
    final label = _statusLabel[status] ?? (status.isEmpty ? '—' : status);
    final color = switch (status) {
      'active' => kGreen,
      'trial' => kInfo,
      'past_due' || 'suspended' => kRed,
      _ => kMuted,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
          color: color.withOpacity(0.12),
          borderRadius: BorderRadius.circular(20)),
      child: Text(label,
          style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: color)),
    );
  }
}

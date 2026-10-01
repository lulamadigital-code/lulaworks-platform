import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';

/// The permission-aware Business History of one canonical record — summary
/// metrics, a chronological timeline, the change/audit trail, and (for jobs)
/// factual outcomes. One facade: GET /business-history/<kind>/<pk>/. Everything
/// here is already money-gated server-side; the app only renders it.
///
/// `kind` ∈ customer | supplier | job | quotation | customer_po |
/// commercial_document.
class BusinessHistoryScreen extends StatefulWidget {
  const BusinessHistoryScreen(
      {super.key, required this.api, required this.kind, required this.id, this.title});
  final ApiClient api;
  final String kind;
  final String id;
  final String? title;

  @override
  State<BusinessHistoryScreen> createState() => _BusinessHistoryScreenState();
}

class _BusinessHistoryScreenState extends State<BusinessHistoryScreen> {
  late Future<Map<String, dynamic>> _future = _load();

  Future<Map<String, dynamic>> _load() async =>
      (await widget.api.get('/business-history/${widget.kind}/${widget.id}/')
              as Map)
          .cast<String, dynamic>();

  static String _date(dynamic iso) {
    final s = '${iso ?? ''}';
    final d = DateTime.tryParse(s);
    if (d == null) return s;
    final l = d.toLocal();
    final mm = l.month.toString().padLeft(2, '0');
    final dd = l.day.toString().padLeft(2, '0');
    return '${l.year}-$mm-$dd';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
          title: Text(widget.title ?? 'Business history'),
          scrolledUnderElevation: 1),
      body: RefreshIndicator(
        onRefresh: () async => setState(() => _future = _load()),
        child: FutureBuilder<Map<String, dynamic>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 100),
                Center(
                    child: Text('${snap.error}', textAlign: TextAlign.center)),
              ]);
            }
            final data = snap.data ?? const {};
            final entity = (data['entity'] as Map?)?.cast<String, dynamic>() ?? const {};
            final summary = (data['summary'] as Map?)?.cast<String, dynamic>() ?? const {};
            final timeline = (data['timeline'] as List?) ?? const [];
            final changes = (data['changes'] as List?) ?? const [];
            final outcomes = (data['outcomes'] as List?) ?? const [];
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text('${entity['label'] ?? widget.title ?? ''}',
                    style: const TextStyle(
                        fontSize: 18, fontWeight: FontWeight.w700, color: kInk)),
                Text('${entity['type'] ?? widget.kind}'.toUpperCase(),
                    style: const TextStyle(
                        fontSize: 11, letterSpacing: .5, color: kMuted)),
                const SizedBox(height: 16),
                if (summary['found'] == true) _summaryCard(summary),
                if (outcomes.isNotEmpty) _outcomesCard(outcomes),
                _section('Timeline', timeline.length),
                if (timeline.isEmpty)
                  _emptyLine('No activity recorded yet.')
                else
                  for (final e in timeline)
                    _timelineTile((e as Map).cast<String, dynamic>()),
                const SizedBox(height: 20),
                _section('Changes', changes.length),
                if (changes.isEmpty)
                  _emptyLine('No tracked changes yet.')
                else
                  for (final c in changes)
                    _changeTile((c as Map).cast<String, dynamic>()),
                const SizedBox(height: 24),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _section(String title, int count) => Padding(
        padding: const EdgeInsets.only(top: 6, bottom: 8),
        child: Row(children: [
          Text(title.toUpperCase(),
              style: const TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  letterSpacing: .5,
                  color: kMuted)),
          const SizedBox(width: 6),
          if (count > 0)
            Text('($count)', style: const TextStyle(fontSize: 11, color: kMuted)),
        ]),
      );

  Widget _emptyLine(String msg) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Text(msg, style: const TextStyle(color: kMuted, fontSize: 13)),
      );

  Widget _card({required Widget child}) => Container(
        margin: const EdgeInsets.only(bottom: 16),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: kLine)),
        child: child,
      );

  Widget _summaryCard(Map<String, dynamic> s) {
    // Only the scalar headline metrics the facade may carry; absent keys skipped.
    final stats = <(String, String)>[
      if (s['job_count'] != null) ('Jobs', '${s['job_count']}'),
      if (s['similar_count'] != null) ('Similar', '${s['similar_count']}'),
      if (s['purchase_count'] != null) ('Purchases', '${s['purchase_count']}'),
      if (s['item_count'] != null) ('Items', '${s['item_count']}'),
      if (s['total_value'] != null) ('Total value', 'R${s['total_value']}'),
    ];
    final src = (s['sources_summary'] as Map?)?.cast<String, dynamic>();
    if (stats.isEmpty && src == null) return const SizedBox.shrink();
    return _card(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('SUMMARY',
            style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w700,
                letterSpacing: .5,
                color: kMuted)),
        const SizedBox(height: 10),
        Wrap(spacing: 20, runSpacing: 12, children: [
          for (final st in stats)
            Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(st.$2,
                  style: const TextStyle(
                      fontSize: 18, fontWeight: FontWeight.w700, color: kInk)),
              Text(st.$1, style: const TextStyle(fontSize: 11.5, color: kMuted)),
            ]),
        ]),
        if (src != null) ...[
          const SizedBox(height: 10),
          Text('Sources: ${src['live'] ?? 0} live · ${src['imported'] ?? 0} imported',
              style: const TextStyle(fontSize: 11.5, color: kMuted)),
        ],
      ]),
    );
  }

  Widget _outcomesCard(List<dynamic> outcomes) {
    return _card(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('OUTCOMES',
            style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w700,
                letterSpacing: .5,
                color: kMuted)),
        const SizedBox(height: 10),
        for (final raw in outcomes)
          _outcomeRow((raw as Map).cast<String, dynamic>()),
      ]),
    );
  }

  Widget _outcomeRow(Map<String, dynamic> o) {
    final type = '${o['type']}';
    final color = type == 'incident' || type == 'over_budget'
        ? kRed
        : (type == 'overdue' ? kOrange : kMuted);
    final extra = o['count'] != null
        ? '${o['count']}'
        : (o['days'] != null ? '${o['days']} days' : '');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(children: [
        Icon(Icons.flag_outlined, size: 16, color: color),
        const SizedBox(width: 8),
        Expanded(child: Text('${o['label'] ?? type}', style: const TextStyle(fontSize: 13.5, color: kInk))),
        if (extra.isNotEmpty)
          Text(extra, style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: color)),
      ]),
    );
  }

  Widget _timelineTile(Map<String, dynamic> e) {
    final amount = '${e['amount'] ?? ''}';
    final detail = '${e['detail'] ?? ''}';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(
            margin: const EdgeInsets.only(top: 4, right: 10),
            width: 8,
            height: 8,
            decoration: const BoxDecoration(color: kBrand, shape: BoxShape.circle)),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(
                  child: Text('${e['title'] ?? ''}',
                      style: const TextStyle(
                          fontWeight: FontWeight.w600, color: kInk, fontSize: 14))),
              if (amount.isNotEmpty)
                Text('R$amount',
                    style: const TextStyle(
                        fontWeight: FontWeight.w700, color: kInk, fontSize: 13)),
            ]),
            if (detail.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(detail, style: const TextStyle(fontSize: 12.5, color: kMuted)),
              ),
            Text(_date(e['when']),
                style: const TextStyle(fontSize: 11.5, color: kMuted)),
          ]),
        ),
      ]),
    );
  }

  Widget _changeTile(Map<String, dynamic> c) {
    final actor = '${c['actor'] ?? ''}';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('${c['summary'] ?? c['type'] ?? ''}',
            style: const TextStyle(fontSize: 13.5, color: kInk)),
        Text('${_date(c['when'])}${actor.isNotEmpty ? ' · $actor' : ''}',
            style: const TextStyle(fontSize: 11.5, color: kMuted)),
      ]),
    );
  }
}

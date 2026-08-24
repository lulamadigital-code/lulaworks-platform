import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';

/// The Finance tab — the portfolio commercial view. Entirely money, so this tab
/// only exists for users with finance.view_money (the shell hides it otherwise,
/// and the backend gates the endpoint regardless). Reads
/// /finance/commercial-dashboard/.
class FinanceScreen extends StatefulWidget {
  const FinanceScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<FinanceScreen> createState() => _FinanceScreenState();
}

class _FinanceScreenState extends State<FinanceScreen> {
  late Future<Map<String, dynamic>> _future = _load();

  Future<Map<String, dynamic>> _load() async =>
      (await widget.api.get('/finance/commercial-dashboard/') as Map)
          .cast<String, dynamic>();

  String _money(dynamic v) {
    final n = double.tryParse('$v') ?? 0;
    final s = n.toStringAsFixed(2);
    // Thousands separators for readability.
    final parts = s.split('.');
    final whole = parts[0].replaceAllMapped(
        RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]} ');
    return '${widget.api.currencySymbol} $whole.${parts[1]}';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Finance')),
      body: RefreshIndicator(
        onRefresh: () async => setState(() { _future = _load(); }),
        child: FutureBuilder<Map<String, dynamic>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 100),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
                const SizedBox(height: 16),
                Center(
                    child: OutlinedButton(
                        onPressed: () => setState(() { _future = _load(); }),
                        child: const Text('Retry'))),
              ]);
            }
            final d = snap.data!;
            final aging = (d['aging'] as Map?)?.cast<String, dynamic>() ?? const {};
            final overdue = double.tryParse('${d['overdue']}') ?? 0;
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _hero(context, 'Outstanding', _money(d['outstanding_invoiced']),
                    Icons.account_balance_wallet,
                    subtitle: overdue > 0
                        ? '${_money(d['overdue'])} overdue'
                        : 'Nothing overdue',
                    subtitleColor: overdue > 0 ? kRed : kGreen),
                const SizedBox(height: 12),
                Row(children: [
                  Expanded(
                      child: _stat(context, 'Revenue', _money(d['revenue']))),
                  const SizedBox(width: 12),
                  Expanded(
                      child: _stat(context, 'Gross profit',
                          _money(d['gross_profit']),
                          note: '${d['margin_pct']}% margin')),
                ]),
                const SizedBox(height: 24),
                Text('Ageing', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 8),
                Card(
                  child: Column(children: [
                    _agingRow(context, 'Current', aging['current']),
                    _agingRow(context, '30 days', aging['30']),
                    _agingRow(context, '60 days', aging['60']),
                    _agingRow(context, '90+ days', aging['90+'], danger: true),
                  ]),
                ),
                if ((d['loss_making_projects'] as List?)?.isNotEmpty == true) ...[
                  const SizedBox(height: 20),
                  Text('Loss-making projects',
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                          color: kRed)),
                  const SizedBox(height: 8),
                  ...(d['loss_making_projects'] as List).map((p) => ListTile(
                        dense: true,
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.trending_down, color: kRed),
                        title: Text('${(p as Map)['name'] ?? p['number'] ?? p}'),
                      )),
                ],
                const SizedBox(height: 24),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _hero(BuildContext context, String label, String value, IconData icon,
      {String? subtitle, Color? subtitleColor}) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Row(children: [
          Container(
            width: 56,
            height: 56,
            decoration: BoxDecoration(
                color: scheme.primary.withOpacity(0.10),
                borderRadius: BorderRadius.circular(14)),
            child: Icon(icon, size: 30, color: scheme.primary),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(label,
                  style: Theme.of(context).textTheme.labelLarge?.copyWith(
                      color: scheme.onSurfaceVariant)),
              const SizedBox(height: 2),
              Text(value,
                  style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.bold)),
              if (subtitle != null)
                Text(subtitle,
                    style: Theme.of(context)
                        .textTheme
                        .bodySmall
                        ?.copyWith(color: subtitleColor)),
            ]),
          ),
        ]),
      ),
    );
  }

  Widget _stat(BuildContext context, String label, String value, {String? note}) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: Theme.of(context).textTheme.labelMedium),
          const SizedBox(height: 4),
          Text(value,
              style: Theme.of(context)
                  .textTheme
                  .titleMedium
                  ?.copyWith(fontWeight: FontWeight.bold)),
          if (note != null)
            Text(note,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: Theme.of(context).colorScheme.outline)),
        ]),
      ),
    );
  }

  Widget _agingRow(BuildContext context, String label, dynamic value,
      {bool danger = false}) {
    final amount = double.tryParse('$value') ?? 0;
    return ListTile(
      dense: true,
      title: Text(label),
      trailing: Text(_money(value),
          style: TextStyle(
              fontWeight: FontWeight.w600,
              color: danger && amount > 0 ? kRed : null)),
    );
  }
}

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/status_pill.dart';
import '../ui/lw_components.dart';
import 'business_history_screen.dart';
import 'customer_po_form_screen.dart';
import 'lulaai_screen.dart';

/// Customer purchase orders — the Sales→Ops bridge. Capture a PO, match it to the
/// quotation it confirms, then convert it into a job. All rules live server-side
/// (/customer-pos/); the app drives them. `value` is money and is withheld by the
/// backend from users without finance.view_money.
class CustomerPosScreen extends StatefulWidget {
  const CustomerPosScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<CustomerPosScreen> createState() => _CustomerPosScreenState();
}

class _CustomerPosScreenState extends State<CustomerPosScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('/customer-pos/'));

  Future<void> _add() async {
    final created = await Navigator.of(context).push<Map<String, dynamic>>(
        MaterialPageRoute(builder: (_) => CustomerPoFormScreen(api: widget.api)));
    if (created == null || !mounted) return;
    await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) =>
            CustomerPoDetailScreen(api: widget.api, poId: '${created['id']}')));
    if (mounted) setState(() => _future = _load());
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Customer POs')),
      floatingActionButton: widget.api.canCreateQuote
          ? FloatingActionButton.extended(
              onPressed: _add,
              icon: const Icon(Icons.add),
              label: const Text('Add PO'))
          : null,
      body: RefreshIndicator(
        onRefresh: () async => setState(() => _future = _load()),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 100),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
              ]);
            }
            final rows = snap.data ?? const [];
            if (rows.isEmpty) {
              return LwEmptyState(
                icon: Icons.assignment_turned_in_outlined,
                title: 'No customer POs yet',
                message: 'Capture a customer purchase order, match it to a '
                    'quotation, and convert it into a job.',
                actionLabel: widget.api.canCreateQuote ? 'Add PO' : null,
                onAction: widget.api.canCreateQuote ? _add : null,
              );
            }
            return ListView.separated(
              itemCount: rows.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final po = rows[i];
                final matched = po['is_matched'] == true;
                return ListTile(
                  leading: Icon(
                      matched ? Icons.link : Icons.link_off,
                      color: matched ? kGreen : kOrange),
                  title: Text('${po['po_number'] ?? ''}',
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  subtitle: Text(
                      '${po['customer_display'] ?? po['client_name'] ?? ''}'
                      '${po['quotation_number'] != null ? ' · ${po['quotation_number']}' : ''}',
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  trailing: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      StatusPill(status: '${po['status']}'),
                      const SizedBox(height: 4),
                      Text(widget.api.money(po['value']),
                          style: const TextStyle(
                              fontSize: 12.5, fontWeight: FontWeight.w600)),
                    ],
                  ),
                  isThreeLine: true,
                  onTap: () async {
                    await Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => CustomerPoDetailScreen(
                            api: widget.api, poId: '${po['id']}')));
                    if (mounted) setState(() => _future = _load());
                  },
                );
              },
            );
          },
        ),
      ),
    );
  }
}

class CustomerPoDetailScreen extends StatefulWidget {
  const CustomerPoDetailScreen({super.key, required this.api, required this.poId});
  final ApiClient api;
  final String poId;

  @override
  State<CustomerPoDetailScreen> createState() => _CustomerPoDetailScreenState();
}

class _CustomerPoDetailScreenState extends State<CustomerPoDetailScreen> {
  late Future<Map<String, dynamic>> _future = _load();
  bool _busy = false;

  Future<Map<String, dynamic>> _load() async =>
      ((await widget.api.getCached('/customer-pos/${widget.poId}/')).data as Map)
          .cast<String, dynamic>();

  void _reload() => setState(() => _future = _load());

  Future<void> _link(String quotationId) async {
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api
          .post('/customer-pos/${widget.poId}/link/', {'quotation': quotationId});
      messenger.showSnackBar(const SnackBar(content: Text('PO linked')));
      _reload();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _linkManually() async {
    List<Map<String, dynamic>> quotes;
    try {
      quotes = pageResults(await widget.api.get('/quotations/'));
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Could not load quotations.')));
      }
      return;
    }
    if (!mounted) return;
    final chosen = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => SafeArea(
        child: ListView(shrinkWrap: true, children: [
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text('Link to a quotation',
                style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
          ),
          for (final q in quotes)
            ListTile(
              title: Text('${q['number'] ?? ''}'),
              subtitle: Text('${q['client_name'] ?? ''}',
                  maxLines: 1, overflow: TextOverflow.ellipsis),
              onTap: () => Navigator.pop(ctx, '${q['id']}'),
            ),
        ]),
      ),
    );
    if (chosen != null) _link(chosen);
  }

  Future<void> _createJob() async {
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final res = await widget.api
          .post('/customer-pos/${widget.poId}/create-job/') as Map;
      final num = (res['project'] as Map?)?['number'] ?? '';
      messenger.showSnackBar(SnackBar(content: Text('Job $num created from PO')));
      _reload();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission to create jobs."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<Map<String, dynamic>>(
      future: _future,
      builder: (context, snap) {
        final po = snap.data;
        return Scaffold(
          appBar: AppBar(
            title: Text('${po?['po_number'] ?? 'Purchase order'}'),
            actions: [
              if (po != null)
                LwActionMenu(actions: [
                  LwAction('Business history', Icons.history, () =>
                      Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => BusinessHistoryScreen(
                            api: widget.api, kind: 'customer_po', id: widget.poId,
                            title: '${po['po_number']}')))),
                  if (widget.api.canGenerateAi)
                    LwAction('Ask LulaAI', Icons.auto_awesome, () =>
                        Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => LulaAiScreen(
                              api: widget.api, ctxType: 'customer_po', ctxId: widget.poId,
                              ctxLabel: 'PO ${po['po_number']}')))),
                ]),
            ],
          ),
          body: po == null
              ? const Center(child: CircularProgressIndicator())
              : _body(po),
        );
      },
    );
  }

  Widget _body(Map<String, dynamic> po) {
    final matched = po['is_matched'] == true;
    final lines = (po['lines'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    final suggestions =
        (po['suggestions'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    final variance = (po['variance'] as Map?)?.cast<String, dynamic>();
    final canEdit = widget.api.canCreateQuote;
    final canCreateJob = widget.api.can('projects.create');
    return ListView(padding: const EdgeInsets.all(16), children: [
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Expanded(
            child: Text('${po['customer_display'] ?? po['client_name'] ?? ''}',
                style: Theme.of(context).textTheme.titleMedium)),
        StatusPill(status: '${po['status']}'),
      ]),
      const SizedBox(height: 6),
      Text('Value: ${widget.api.money(po['value'])}'
          '${po['po_date'] != null ? '  ·  ${po['po_date']}' : ''}',
          style: Theme.of(context).textTheme.bodyMedium),
      if (matched && po['quotation_number'] != null) ...[
        const SizedBox(height: 4),
        Row(children: [
          Icon(Icons.link, size: 15, color: kGreen),
          const SizedBox(width: 4),
          Text('Linked to ${po['quotation_number']}',
              style: TextStyle(color: kGreen, fontWeight: FontWeight.w600)),
        ]),
      ],
      if (variance != null && variance['has_variance'] == true) ...[
        const SizedBox(height: 10),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
              color: kOrange.withOpacity(0.08),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: kOrange.withOpacity(0.4))),
          child: Row(children: [
            Icon(Icons.warning_amber, size: 18, color: kOrange),
            const SizedBox(width: 8),
            Expanded(child: Text('${variance['message'] ?? 'Value variance.'}',
                style: const TextStyle(fontSize: 12.5))),
          ]),
        ),
      ],
      const SizedBox(height: 16),

      if (lines.isNotEmpty) ...[
        Text('Lines (${lines.length})',
            style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: 6),
        for (final l in lines)
          Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              dense: true,
              title: Text('${l['description'] ?? '—'}'),
              subtitle: Text('Qty ${l['qty'] ?? '—'}'
                  '${l['unit'] != null ? ' ${l['unit']}' : ''}'),
              trailing: Text(widget.api.money(l['unit_price']),
                  style: const TextStyle(fontWeight: FontWeight.w600)),
            ),
          ),
        const SizedBox(height: 8),
      ],

      // Unmatched → suggestions + manual link.
      if (!matched && canEdit) ...[
        Text('Match to a quotation',
            style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: 6),
        if (suggestions.isEmpty)
          Text('No suggestions — link manually.',
              style: TextStyle(color: kMuted, fontSize: 13)),
        for (final s in suggestions)
          Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              title: Text('${s['number'] ?? ''}'),
              subtitle: Text('${s['client_name'] ?? ''} · ${s['reason'] ?? ''}',
                  maxLines: 1, overflow: TextOverflow.ellipsis),
              trailing: _busy
                  ? const SizedBox(
                      width: 16, height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : TextButton(
                      onPressed: () => _link('${s['quotation']}'),
                      child: const Text('Link')),
            ),
          ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: _busy ? null : _linkManually,
          icon: const Icon(Icons.search, size: 18),
          label: const Text('Link to another quotation'),
        ),
      ],

      // Matched → create job.
      if (matched && canCreateJob && '${po['status']}' != 'in_progress') ...[
        const SizedBox(height: 8),
        FilledButton.icon(
          onPressed: _busy ? null : _createJob,
          icon: const Icon(Icons.construction),
          label: const Text('Create job from PO'),
        ),
      ],
      const SizedBox(height: 24),
    ]);
  }
}

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import 'customer_detail_screen.dart';

/// One lead — its details and the two decisions that move it out of the
/// pipeline: convert to a customer, or mark it lost. Mirrors the web CRM.
class LeadDetailScreen extends StatefulWidget {
  const LeadDetailScreen({super.key, required this.api, required this.leadId});
  final ApiClient api;
  final String leadId;

  @override
  State<LeadDetailScreen> createState() => _LeadDetailScreenState();
}

class _LeadDetailScreenState extends State<LeadDetailScreen> {
  late Future<Map<String, dynamic>> _future = _load();
  bool _busy = false;

  Future<Map<String, dynamic>> _load() async =>
      (await widget.api.get('/leads/${widget.leadId}/') as Map).cast<String, dynamic>();

  bool get _canEdit => widget.api.can('crm.manage');

  Future<void> _convert() async {
    setState(() => _busy = true);
    try {
      final r = await widget.api.post('/leads/${widget.leadId}/convert/', {});
      final cid = (r as Map)['customer_id']?.toString();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Lead converted to a customer.')));
      if (cid != null) {
        Navigator.of(context).pushReplacement(MaterialPageRoute(
            builder: (_) => CustomerDetailScreen(api: widget.api, customerId: cid)));
      } else {
        setState(() => _future = _load());
      }
    } on ApiException catch (e) {
      _snack(e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _markLost() async {
    final reason = await showDialog<String>(
      context: context,
      builder: (ctx) {
        final c = TextEditingController();
        return AlertDialog(
          title: const Text('Mark lead as lost'),
          content: TextField(
            controller: c,
            decoration: const InputDecoration(labelText: 'Reason (optional)'),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: const Color(0xFFC0392B)),
              onPressed: () => Navigator.pop(ctx, c.text.trim()),
              child: const Text('Mark lost')),
          ],
        );
      },
    );
    if (reason == null) return;
    setState(() => _busy = true);
    try {
      await widget.api.post('/leads/${widget.leadId}/lost/', {'reason': reason});
      if (mounted) setState(() => _future = _load());
    } on ApiException catch (e) {
      _snack(e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _snack(String m) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Lead')),
      body: FutureBuilder<Map<String, dynamic>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator(color: kBrand));
          }
          if (!snap.hasData) {
            return const Center(child: Text('Could not load the lead.',
                style: TextStyle(color: kMuted)));
          }
          final l = snap.data!;
          final status = '${l['status']}';
          final open = status != 'converted' && status != 'lost';
          final value = double.tryParse('${l['estimated_value'] ?? 0}') ?? 0;
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Text('${l['company_name']}',
                  style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700, color: kInk)),
              const SizedBox(height: 4),
              Text('${l['status_display'] ?? status}', style: const TextStyle(color: kMuted)),
              const SizedBox(height: 16),
              _row('Contact', '${l['contact_name'] ?? ''}'),
              _row('Email', '${l['email'] ?? ''}'),
              _row('Mobile', '${l['mobile'] ?? ''}'),
              _row('Industry', '${l['industry'] ?? ''}'),
              _row('City', '${l['city'] ?? ''}'),
              _row('Source', '${l['source'] ?? ''}'),
              if (value > 0) _row('Estimated value', widget.api.money(value)),
              if ('${l['notes'] ?? ''}'.isNotEmpty) _row('Notes', '${l['notes']}'),
              if (status == 'lost' && '${l['lost_reason'] ?? ''}'.isNotEmpty)
                _row('Lost reason', '${l['lost_reason']}'),
              const SizedBox(height: 20),
              if (open && _canEdit) ...[
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    style: FilledButton.styleFrom(backgroundColor: kBrand),
                    onPressed: _busy ? null : _convert,
                    icon: const Icon(Icons.how_to_reg),
                    label: const Text('Convert to customer'),
                  ),
                ),
                const SizedBox(height: 10),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    onPressed: _busy ? null : _markLost,
                    icon: const Icon(Icons.cancel_outlined, color: Color(0xFFC0392B)),
                    label: const Text('Mark as lost',
                        style: TextStyle(color: Color(0xFFC0392B))),
                  ),
                ),
              ],
            ],
          );
        },
      ),
    );
  }

  Widget _row(String label, String value) {
    if (value.trim().isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 120, child: Text(label, style: const TextStyle(color: kMuted, fontSize: 13))),
        Expanded(child: Text(value, style: const TextStyle(color: kInk, fontSize: 14))),
      ]),
    );
  }
}

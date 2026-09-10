import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import 'lead_detail_screen.dart';

/// Sales leads — prospects before they become customers. List, add, and open a
/// lead to convert it to a customer or mark it lost. Parity with the web CRM.
class LeadsScreen extends StatefulWidget {
  const LeadsScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<LeadsScreen> createState() => _LeadsScreenState();
}

class _LeadsScreenState extends State<LeadsScreen> {
  String _filter = 'open'; // open | all
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async {
    final q = _filter == 'open' ? '?status=open' : '';
    final body = await widget.api.get('/leads/$q');
    return pageResults(body).cast<Map<String, dynamic>>();
  }

  void _reload() => setState(() => _future = _load());

  bool get _canEdit => widget.api.can('crm.manage');

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Leads'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(46),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
            child: Row(children: [
              _chip('Open', 'open'),
              const SizedBox(width: 8),
              _chip('All', 'all'),
            ]),
          ),
        ),
      ),
      floatingActionButton: _canEdit
          ? FloatingActionButton.extended(
              backgroundColor: kBrand,
              onPressed: _addLead,
              icon: const Icon(Icons.add),
              label: const Text('Add lead'))
          : null,
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => _reload(),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator(color: kBrand));
            }
            if (snap.hasError) {
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('Could not load leads.',
                    style: TextStyle(color: kMuted))),
              ]);
            }
            final leads = snap.data!;
            if (leads.isEmpty) {
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('No leads yet.', style: TextStyle(color: kMuted))),
              ]);
            }
            return ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: leads.length,
              separatorBuilder: (_, __) => const SizedBox(height: 10),
              itemBuilder: (_, i) => _leadCard(leads[i]),
            );
          },
        ),
      ),
    );
  }

  Widget _chip(String label, String value) {
    final on = _filter == value;
    return ChoiceChip(
      label: Text(label),
      selected: on,
      selectedColor: kBrandTint,
      onSelected: (_) { setState(() => _filter = value); _reload(); },
    );
  }

  Widget _leadCard(Map<String, dynamic> l) {
    final status = '${l['status']}';
    final value = double.tryParse('${l['estimated_value'] ?? 0}') ?? 0;
    return InkWell(
      borderRadius: BorderRadius.circular(12),
      onTap: () async {
        await Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => LeadDetailScreen(api: widget.api, leadId: '${l['id']}')));
        _reload();
      },
      child: Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: kLine),
        ),
        child: Row(children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${l['company_name']}',
                  style: const TextStyle(fontWeight: FontWeight.w600, color: kInk)),
              if ('${l['contact_name']}'.isNotEmpty)
                Text('${l['contact_name']}',
                    style: const TextStyle(fontSize: 12.5, color: kMuted)),
              if (value > 0)
                Text(widget.api.money(value),
                    style: const TextStyle(fontSize: 12.5, color: kMuted)),
            ]),
          ),
          _statusPill(status, '${l['status_display'] ?? status}'),
        ]),
      ),
    );
  }

  Widget _statusPill(String status, String label) {
    final c = switch (status) {
      'converted' => kGreen,
      'lost' => const Color(0xFFC0392B),
      'qualified' => kBrandDark,
      _ => kMuted,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
          color: c.withOpacity(0.12), borderRadius: BorderRadius.circular(20)),
      child: Text(label, style: TextStyle(fontSize: 11.5, color: c, fontWeight: FontWeight.w600)),
    );
  }

  Future<void> _addLead() async {
    final created = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _LeadForm(api: widget.api),
    );
    if (created == true) _reload();
  }
}

class _LeadForm extends StatefulWidget {
  const _LeadForm({required this.api});
  final ApiClient api;
  @override
  State<_LeadForm> createState() => _LeadFormState();
}

class _LeadFormState extends State<_LeadForm> {
  final _company = TextEditingController();
  final _contact = TextEditingController();
  final _email = TextEditingController();
  final _mobile = TextEditingController();
  final _value = TextEditingController();
  final _notes = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [_company, _contact, _email, _mobile, _value, _notes]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    if (_company.text.trim().isEmpty) {
      setState(() => _error = 'A company name is required.');
      return;
    }
    setState(() { _busy = true; _error = null; });
    try {
      await widget.api.post('/leads/', {
        'company_name': _company.text.trim(),
        if (_contact.text.trim().isNotEmpty) 'contact_name': _contact.text.trim(),
        if (_email.text.trim().isNotEmpty) 'email': _email.text.trim(),
        if (_mobile.text.trim().isNotEmpty) 'mobile': _mobile.text.trim(),
        if (_value.text.trim().isNotEmpty) 'estimated_value': _value.text.trim(),
        if (_notes.text.trim().isNotEmpty) 'notes': _notes.text.trim(),
      });
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Could not save the lead.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bottom = MediaQuery.of(context).viewInsets.bottom;
    return Padding(
      padding: EdgeInsets.fromLTRB(16, 16, 16, 16 + bottom),
      child: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('New lead', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700, color: kInk)),
          const SizedBox(height: 14),
          _field(_company, 'Company name *'),
          _field(_contact, 'Contact name'),
          _field(_email, 'Email', keyboard: TextInputType.emailAddress),
          _field(_mobile, 'Mobile', keyboard: TextInputType.phone),
          _field(_value, 'Estimated value', keyboard: TextInputType.number),
          _field(_notes, 'Notes', lines: 3),
          if (_error != null) ...[
            const SizedBox(height: 6),
            Text(_error!, style: const TextStyle(color: Color(0xFFC0392B), fontSize: 13)),
          ],
          const SizedBox(height: 14),
          SizedBox(
            width: double.infinity,
            child: FilledButton(
              style: FilledButton.styleFrom(backgroundColor: kBrand),
              onPressed: _busy ? null : _save,
              child: _busy
                  ? const SizedBox(height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Text('Save lead'),
            ),
          ),
        ]),
      ),
    );
  }

  Widget _field(TextEditingController c, String label,
      {TextInputType? keyboard, int lines = 1}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: TextField(
        controller: c,
        keyboardType: keyboard,
        minLines: lines,
        maxLines: lines,
        decoration: InputDecoration(labelText: label, border: const OutlineInputBorder()),
      ),
    );
  }
}

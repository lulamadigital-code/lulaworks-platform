import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

class _Line {
  final desc = TextEditingController();
  final qty = TextEditingController(text: '1');
  final price = TextEditingController();
  void dispose() {
    desc.dispose();
    qty.dispose();
    price.dispose();
  }
}

/// Create a DIRECT tax invoice (no prior quote). Posts to /commercial-documents/
/// (quotes.create); the backend builds the invoice with canonical numbering.
class InvoiceFormScreen extends StatefulWidget {
  const InvoiceFormScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<InvoiceFormScreen> createState() => _InvoiceFormScreenState();
}

class _InvoiceFormScreenState extends State<InvoiceFormScreen> {
  final _client = TextEditingController();
  final _title = TextEditingController();
  final _vat = TextEditingController();
  final _notes = TextEditingController();
  final List<_Line> _lines = [_Line()];
  String? _customerId;
  List<Map<String, dynamic>> _customers = const [];
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadCustomers();
  }

  Future<void> _loadCustomers() async {
    try {
      final rows = pageResults(await widget.api.get('/customers/'));
      if (mounted) setState(() => _customers = rows);
    } catch (_) {/* optional */}
  }

  @override
  void dispose() {
    _client.dispose();
    _title.dispose();
    _vat.dispose();
    _notes.dispose();
    for (final l in _lines) {
      l.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    if (_client.text.trim().isEmpty) {
      setState(() => _error = 'A client name is required.');
      return;
    }
    final lines = <Map<String, dynamic>>[];
    for (final l in _lines) {
      final d = l.desc.text.trim();
      if (d.isEmpty) continue;
      lines.add({
        'description': d,
        'qty': double.tryParse(l.qty.text.trim()) ?? 1,
        'unit_price': double.tryParse(l.price.text.trim()) ?? 0,
      });
    }
    if (lines.isEmpty) {
      setState(() => _error = 'Add at least one line item to invoice.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final body = <String, dynamic>{
      'client_name': _client.text.trim(),
      'title': _title.text.trim(),
      'notes': _notes.text.trim(),
      'lines': lines,
      if (_customerId != null) 'customer': _customerId,
      if (_vat.text.trim().isNotEmpty) 'vat_rate': double.tryParse(_vat.text.trim()),
    };
    final messenger = ScaffoldMessenger.of(context);
    try {
      final doc = await widget.api.post('/commercial-documents/', body) as Map;
      if (!mounted) return;
      messenger.showSnackBar(
          SnackBar(content: Text('Invoice ${doc['number'] ?? ''} created')));
      Navigator.of(context).pop(doc);
    } on ApiException catch (e) {
      setState(() => _error = e.isForbidden
          ? "You don't have permission to create invoices."
          : e.message);
    } catch (_) {
      setState(() => _error = 'Could not reach the server.');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Widget _lineCard(int i) {
    final l = _lines[i];
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
          border: Border.all(color: kLine),
          borderRadius: BorderRadius.circular(12)),
      child: Column(children: [
        Row(children: [
          Text('Item ${i + 1}',
              style: const TextStyle(fontWeight: FontWeight.w600, color: kInk)),
          const Spacer(),
          if (_lines.length > 1)
            IconButton(
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.close, size: 18, color: kMuted),
                onPressed: () => setState(() {
                      _lines[i].dispose();
                      _lines.removeAt(i);
                    })),
        ]),
        LulaTextField(controller: l.desc, label: 'Description'),
        const SizedBox(height: 10),
        Row(children: [
          Expanded(
              child: LulaTextField(
                  controller: l.qty,
                  label: 'Qty',
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true))),
          const SizedBox(width: 10),
          Expanded(
              child: LulaTextField(
                  controller: l.price,
                  label: 'Unit price',
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true))),
        ]),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('New invoice'), scrolledUnderElevation: 1),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          if (_customers.isNotEmpty) ...[
            LulaDropdown<String>(
              label: 'Customer (optional)',
              value: _customerId,
              items: [
                const DropdownMenuItem(value: null, child: Text('Not linked')),
                for (final cst in _customers)
                  DropdownMenuItem(
                      value: '${cst['id']}',
                      child: Text('${cst['name'] ?? ''}',
                          overflow: TextOverflow.ellipsis)),
              ],
              onChanged: (v) => setState(() {
                _customerId = v;
                if (v != null && _client.text.trim().isEmpty) {
                  final m = _customers.firstWhere((c) => '${c['id']}' == v,
                      orElse: () => const {});
                  _client.text = '${m['name'] ?? ''}';
                }
              }),
            ),
            const SizedBox(height: 16),
          ],
          LulaTextField(controller: _client, label: 'Client name', required: true),
          const SizedBox(height: 16),
          LulaTextField(controller: _title, label: 'Title (optional)'),
          const SizedBox(height: 16),
          LulaTextField(
              controller: _vat,
              label: 'VAT % (optional)',
              keyboardType: const TextInputType.numberWithOptions(decimal: true)),
          const SizedBox(height: 16),
          LulaTextField(
              controller: _notes, label: 'Notes / terms (optional)', maxLines: 3),
          const SizedBox(height: 22),
          const Text('Line items',
              style: TextStyle(fontWeight: FontWeight.w700, color: kInk)),
          const SizedBox(height: 12),
          for (var i = 0; i < _lines.length; i++) _lineCard(i),
          OutlinedButton.icon(
            onPressed: () => setState(() => _lines.add(_Line())),
            icon: const Icon(Icons.add, size: 18),
            label: const Text('Add item'),
          ),
          if (_error != null) ...[
            const SizedBox(height: 16),
            Text(_error!, style: const TextStyle(color: kRed, fontSize: 13)),
          ],
          const SizedBox(height: 22),
          LulaButton(
              label: 'Create invoice',
              loadingLabel: 'Saving…',
              loading: _saving,
              onPressed: _save),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

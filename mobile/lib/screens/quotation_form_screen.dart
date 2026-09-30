import 'package:flutter/material.dart';

import '../api/api_client.dart';
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

/// Create a quotation: client, title, site and optional line items. Posts to
/// /quotations/ (quotes.create). The backend numbers it and computes totals.
class QuotationFormScreen extends StatefulWidget {
  const QuotationFormScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<QuotationFormScreen> createState() => _QuotationFormScreenState();
}

class _QuotationFormScreenState extends State<QuotationFormScreen> {
  final _client = TextEditingController();
  final _title = TextEditingController();
  final _site = TextEditingController();
  final List<_Line> _lines = [_Line()];
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _client.dispose();
    _title.dispose();
    _site.dispose();
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
    // Only lines with a description are sent.
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
    setState(() {
      _saving = true;
      _error = null;
    });
    final body = <String, dynamic>{
      'client_name': _client.text.trim(),
      'title': _title.text.trim(),
      'site': _site.text.trim(),
      'lines': lines,
    };
    final messenger = ScaffoldMessenger.of(context);
    try {
      final saved = await widget.api.post('/quotations/', body);
      if (!mounted) return;
      messenger.showSnackBar(const SnackBar(content: Text('Quotation created')));
      Navigator.of(context).pop(saved);
    } on ApiException catch (e) {
      setState(() => _error = e.isForbidden
          ? "You don't have permission to create quotations."
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
      appBar:
          AppBar(title: const Text('New quotation'), scrolledUnderElevation: 1),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          LulaTextField(
              controller: _client, label: 'Client name', required: true),
          const SizedBox(height: 16),
          LulaTextField(controller: _title, label: 'Title (optional)'),
          const SizedBox(height: 16),
          LulaTextField(controller: _site, label: 'Site (optional)'),
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
              label: 'Create quotation',
              loadingLabel: 'Saving…',
              loading: _saving,
              onPressed: _save),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

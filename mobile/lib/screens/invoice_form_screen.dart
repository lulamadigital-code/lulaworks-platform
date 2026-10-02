import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../ui/tokens.dart';
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

  double get lineTotal =>
      (double.tryParse(qty.text.trim()) ?? 0) * (double.tryParse(price.text.trim()) ?? 0);
}

/// Create a DIRECT tax invoice (no prior quote) as a guided 3-step workflow
/// (design brief §15): 1 · Customer  2 · Items  3 · Pricing & terms. Totals are
/// a live preview; the backend builds the invoice with canonical numbering and
/// authoritative figures. Posts to /commercial-documents/ (quotes.create).
class InvoiceFormScreen extends StatefulWidget {
  const InvoiceFormScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<InvoiceFormScreen> createState() => _InvoiceFormScreenState();
}

class _InvoiceFormScreenState extends State<InvoiceFormScreen> {
  static const _steps = ['Customer', 'Items', 'Pricing & terms'];

  final _client = TextEditingController();
  final _title = TextEditingController();
  final _vat = TextEditingController();
  final _notes = TextEditingController();
  final List<_Line> _lines = [_Line()];
  String? _customerId;
  List<Map<String, dynamic>> _customers = const [];

  int _step = 0;
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

  double get _subtotal => _lines.fold(0, (s, l) => s + l.lineTotal);
  double get _vatRate => double.tryParse(_vat.text.trim()) ?? 0;
  double get _vatAmount => _subtotal * _vatRate / 100;
  double get _total => _subtotal + _vatAmount;
  String _money(double v) => 'R${v.toStringAsFixed(2)}';

  bool get _hasLine => _lines.any((l) => l.desc.text.trim().isNotEmpty);

  bool get _canContinue {
    if (_step == 0) return _client.text.trim().isNotEmpty;
    if (_step == 1) return _hasLine;
    return true;
  }

  void _next() {
    FocusScope.of(context).unfocus();
    if (_step == 0 && _client.text.trim().isEmpty) {
      setState(() => _error = 'A client name is required.');
      return;
    }
    if (_step == 1 && !_hasLine) {
      setState(() => _error = 'Add at least one line item to invoice.');
      return;
    }
    setState(() {
      _error = null;
      if (_step < _steps.length - 1) _step++;
    });
  }

  void _back() {
    FocusScope.of(context).unfocus();
    setState(() {
      _error = null;
      if (_step > 0) _step--;
    });
  }

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    if (_client.text.trim().isEmpty) {
      setState(() {
        _step = 0;
        _error = 'A client name is required.';
      });
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
      setState(() {
        _step = 1;
        _error = 'Add at least one line item to invoice.';
      });
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
      if (_vat.text.trim().isNotEmpty) 'vat_rate': _vatRate,
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

  @override
  Widget build(BuildContext context) {
    final isLast = _step == _steps.length - 1;
    return Scaffold(
      appBar: AppBar(title: const Text('New invoice'), scrolledUnderElevation: 1),
      body: Column(children: [
        _stepIndicator(),
        const Divider(height: 1),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.all(LwSpace.xl),
            children: [
              if (_step == 0) ..._customerStep(),
              if (_step == 1) ..._itemsStep(),
              if (_step == 2) ..._pricingStep(),
              if (_error != null) ...[
                const SizedBox(height: LwSpace.lg),
                Text(_error!, style: const TextStyle(color: kRed, fontSize: 13)),
              ],
              const SizedBox(height: LwSpace.xxl),
            ],
          ),
        ),
        _bottomBar(isLast),
      ]),
    );
  }

  Widget _stepIndicator() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(LwSpace.xl, LwSpace.md, LwSpace.xl, LwSpace.md),
      child: Row(children: [
        for (var i = 0; i < _steps.length; i++) ...[
          _dot(i),
          if (i < _steps.length - 1)
            Expanded(
              child: Container(
                height: 2,
                margin: const EdgeInsets.symmetric(horizontal: 6),
                color: i < _step ? kBrand : kLine,
              ),
            ),
        ],
      ]),
    );
  }

  Widget _dot(int i) {
    final done = i < _step;
    final active = i == _step;
    return Container(
      width: 26,
      height: 26,
      decoration: BoxDecoration(
        color: active || done ? kBrand : Colors.white,
        shape: BoxShape.circle,
        border: Border.all(color: active || done ? kBrand : kLine, width: 1.5),
      ),
      child: done
          ? const Icon(Icons.check, size: 15, color: Colors.white)
          : Center(
              child: Text('${i + 1}',
                  style: TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w700,
                      color: active ? Colors.white : kMuted))),
    );
  }

  List<Widget> _customerStep() {
    return [
      Text(_steps[0], style: LwType.title),
      const SizedBox(height: LwSpace.lg),
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
        const SizedBox(height: LwSpace.lg),
      ],
      LulaTextField(controller: _client, label: 'Client name', required: true),
      const SizedBox(height: LwSpace.lg),
      LulaTextField(controller: _title, label: 'Title (optional)'),
    ];
  }

  List<Widget> _itemsStep() {
    return [
      Row(children: [
        Text(_steps[1], style: LwType.title),
        const Spacer(),
        Text('Subtotal ${_money(_subtotal)}', style: LwType.caption),
      ]),
      const SizedBox(height: LwSpace.md),
      for (var i = 0; i < _lines.length; i++) _lineCard(i),
      OutlinedButton.icon(
        onPressed: () => setState(() => _lines.add(_Line())),
        icon: const Icon(Icons.add, size: 18),
        label: const Text('Add item'),
      ),
    ];
  }

  Widget _lineCard(int i) {
    final l = _lines[i];
    return Container(
      margin: const EdgeInsets.only(bottom: LwSpace.md),
      padding: const EdgeInsets.all(LwSpace.md),
      decoration: BoxDecoration(border: Border.all(color: kLine), borderRadius: LwRadius.card),
      child: Column(children: [
        Row(children: [
          Text('Item ${i + 1}', style: LwType.label),
          const Spacer(),
          Text(_money(l.lineTotal), style: LwType.caption),
          if (_lines.length > 1)
            IconButton(
                visualDensity: VisualDensity.compact,
                tooltip: 'Remove item',
                icon: const Icon(Icons.close, size: 18, color: kMuted),
                onPressed: () => setState(() {
                      _lines[i].dispose();
                      _lines.removeAt(i);
                    })),
        ]),
        LulaTextField(controller: l.desc, label: 'Description', onChanged: (_) => setState(() {})),
        const SizedBox(height: LwSpace.sm),
        Row(children: [
          Expanded(
              child: LulaTextField(
                  controller: l.qty,
                  label: 'Qty',
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  onChanged: (_) => setState(() {}))),
          const SizedBox(width: LwSpace.sm),
          Expanded(
              child: LulaTextField(
                  controller: l.price,
                  label: 'Unit price',
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  onChanged: (_) => setState(() {}))),
        ]),
      ]),
    );
  }

  List<Widget> _pricingStep() {
    return [
      Text(_steps[2], style: LwType.title),
      const SizedBox(height: LwSpace.lg),
      LulaTextField(
          controller: _vat,
          label: 'VAT % (optional)',
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          onChanged: (_) => setState(() {})),
      const SizedBox(height: LwSpace.lg),
      LulaTextField(controller: _notes, label: 'Notes / terms (optional)', maxLines: 3),
      const SizedBox(height: LwSpace.xl),
      Container(
        padding: const EdgeInsets.all(LwSpace.lg),
        decoration: const BoxDecoration(color: kBrandTint, borderRadius: LwRadius.card),
        child: Column(children: [
          _totalRow('Subtotal', _money(_subtotal)),
          const SizedBox(height: LwSpace.sm),
          _totalRow('VAT (${_vatRate.toStringAsFixed(_vatRate % 1 == 0 ? 0 : 2)}%)',
              _money(_vatAmount)),
          const Padding(
            padding: EdgeInsets.symmetric(vertical: LwSpace.sm),
            child: Divider(height: 1),
          ),
          _totalRow('Total', _money(_total), strong: true),
        ]),
      ),
      const SizedBox(height: LwSpace.sm),
      const Text('Totals are a preview — the server confirms the final figures '
          'on the invoice.', style: LwType.caption),
    ];
  }

  Widget _totalRow(String label, String value, {bool strong = false}) {
    final style = strong
        ? const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: kInk)
        : LwType.body;
    return Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
      Text(label, style: style),
      Text(value, style: style),
    ]);
  }

  Widget _bottomBar(bool isLast) {
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.all(LwSpace.lg),
        decoration: const BoxDecoration(
          color: Colors.white,
          border: Border(top: BorderSide(color: kLine)),
        ),
        child: Row(children: [
          if (_step > 0)
            Expanded(
              child: OutlinedButton(
                onPressed: _saving ? null : _back,
                child: const Text('Back'),
              ),
            ),
          if (_step > 0) const SizedBox(width: LwSpace.md),
          Expanded(
            flex: 2,
            child: isLast
                ? LulaButton(
                    label: 'Create invoice',
                    loadingLabel: 'Creating…',
                    loading: _saving,
                    onPressed: _save)
                : FilledButton(
                    onPressed: _canContinue ? _next : null,
                    style: FilledButton.styleFrom(
                        minimumSize: const Size.fromHeight(48)),
                    child: const Text('Continue'),
                  ),
          ),
        ]),
      ),
    );
  }
}

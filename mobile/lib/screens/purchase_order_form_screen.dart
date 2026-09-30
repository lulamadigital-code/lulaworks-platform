import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

class _Line {
  final desc = TextEditingController();
  final qty = TextEditingController(text: '1');
  final unit = TextEditingController(text: 'each');
  final price = TextEditingController();
  void dispose() {
    desc.dispose();
    qty.dispose();
    unit.dispose();
    price.dispose();
  }
}

/// Raise a purchase order (us → supplier). Posts to /purchase-orders/
/// (procurement.manage); the backend numbers it and inherits the supplier's
/// payment terms. Money is Golden-Rule gated on read-back.
class PurchaseOrderFormScreen extends StatefulWidget {
  const PurchaseOrderFormScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<PurchaseOrderFormScreen> createState() =>
      _PurchaseOrderFormScreenState();
}

class _PurchaseOrderFormScreenState extends State<PurchaseOrderFormScreen> {
  final _deliveryAddr = TextEditingController();
  final List<_Line> _lines = [_Line()];
  String? _supplierId;
  List<Map<String, dynamic>> _suppliers = const [];
  bool _loadingSuppliers = true;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadSuppliers();
  }

  Future<void> _loadSuppliers() async {
    try {
      final rows = pageResults(await widget.api.get('/suppliers/'));
      if (mounted) setState(() => _suppliers = rows);
    } catch (_) {/* surfaced below as an empty picker */} finally {
      if (mounted) setState(() => _loadingSuppliers = false);
    }
  }

  @override
  void dispose() {
    _deliveryAddr.dispose();
    for (final l in _lines) {
      l.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    if (_supplierId == null) {
      setState(() => _error = 'Choose a supplier.');
      return;
    }
    final lines = <Map<String, dynamic>>[];
    for (final l in _lines) {
      final d = l.desc.text.trim();
      if (d.isEmpty) continue;
      lines.add({
        'description': d,
        'qty': double.tryParse(l.qty.text.trim()) ?? 1,
        'unit': l.unit.text.trim().isEmpty ? 'each' : l.unit.text.trim(),
        'unit_price': double.tryParse(l.price.text.trim()) ?? 0,
      });
    }
    if (lines.isEmpty) {
      setState(() => _error = 'Add at least one line item to order.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final body = <String, dynamic>{
      'supplier': _supplierId,
      'delivery_address': _deliveryAddr.text.trim(),
      'lines': lines,
    };
    final messenger = ScaffoldMessenger.of(context);
    try {
      final po = await widget.api.post('/purchase-orders/', body) as Map;
      if (!mounted) return;
      messenger.showSnackBar(
          SnackBar(content: Text('PO ${po['number'] ?? ''} created')));
      Navigator.of(context).pop(po);
    } on ApiException catch (e) {
      setState(() => _error = e.isForbidden
          ? "You don't have permission to create purchase orders."
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
          Expanded(child: LulaTextField(controller: l.unit, label: 'Unit')),
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
      appBar: AppBar(
          title: const Text('New purchase order'), scrolledUnderElevation: 1),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          if (_loadingSuppliers)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 8),
              child: LinearProgressIndicator(),
            )
          else if (_suppliers.isEmpty)
            const Text('No suppliers yet — add a supplier first.',
                style: TextStyle(color: kMuted))
          else
            LulaDropdown<String>(
              label: 'Supplier',
              value: _supplierId,
              items: [
                for (final s in _suppliers)
                  DropdownMenuItem(
                      value: '${s['id']}',
                      child: Text('${s['name'] ?? ''}',
                          overflow: TextOverflow.ellipsis)),
              ],
              onChanged: (v) => setState(() => _supplierId = v),
            ),
          const SizedBox(height: 16),
          LulaTextField(
              controller: _deliveryAddr,
              label: 'Delivery address (optional)',
              maxLines: 2),
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
              label: 'Create purchase order',
              loadingLabel: 'Saving…',
              loading: _saving,
              onPressed: _save),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

/// Create or edit a supplier. Writes go to /suppliers/ (gated procurement.manage
/// server-side). Banking/performance are money-gated and managed on the web, so
/// this form stays to the sourcing essentials.
class SupplierFormScreen extends StatefulWidget {
  const SupplierFormScreen({super.key, required this.api, this.existing});
  final ApiClient api;
  final Map<String, dynamic>? existing; // null = create

  @override
  State<SupplierFormScreen> createState() => _SupplierFormScreenState();
}

class _SupplierFormScreenState extends State<SupplierFormScreen> {
  // (key, label, keyboard, required, multiline)
  static const _textFields = <(String, String, TextInputType, bool, bool)>[
    ('name', 'Supplier name', TextInputType.text, true, false),
    ('contact_person', 'Contact person', TextInputType.text, false, false),
    ('email', 'Email', TextInputType.emailAddress, false, false),
    ('phone', 'Phone', TextInputType.phone, false, false),
    ('registration_no', 'Registration number', TextInputType.text, false, false),
    ('vat_no', 'VAT number', TextInputType.text, false, false),
    ('payment_terms', 'Payment terms', TextInputType.text, false, false),
    ('notes', 'Notes', TextInputType.multiline, false, true),
  ];

  final _c = <String, TextEditingController>{};
  final _categories = TextEditingController();
  final _bee = TextEditingController();
  bool _preferred = false;
  bool _saving = false;
  String? _error;

  bool get _isEdit => widget.existing != null;

  @override
  void initState() {
    super.initState();
    final e = widget.existing ?? const {};
    for (final f in _textFields) {
      _c[f.$1] = TextEditingController(text: '${e[f.$1] ?? ''}');
    }
    _categories.text = ((e['categories'] as List?)?.join(', ')) ?? '';
    _bee.text = e['bee_level'] == null ? '' : '${e['bee_level']}';
    _preferred = e['preferred'] == true;
  }

  @override
  void dispose() {
    for (final c in _c.values) {
      c.dispose();
    }
    _categories.dispose();
    _bee.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    if (_c['name']!.text.trim().isEmpty) {
      setState(() => _error = 'A supplier name is required.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final cats = _categories.text
        .split(',')
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .toList();
    final body = <String, dynamic>{
      for (final f in _textFields) f.$1: _c[f.$1]!.text.trim(),
      'categories': cats,
      'preferred': _preferred,
      if (_bee.text.trim().isNotEmpty) 'bee_level': int.tryParse(_bee.text.trim()),
    };
    final messenger = ScaffoldMessenger.of(context);
    try {
      final saved = _isEdit
          ? await widget.api.patch('/suppliers/${widget.existing!['id']}/', body)
          : await widget.api.post('/suppliers/', body);
      if (!mounted) return;
      messenger.showSnackBar(
          SnackBar(content: Text(_isEdit ? 'Supplier saved' : 'Supplier created')));
      Navigator.of(context).pop(saved);
    } on ApiException catch (e) {
      setState(() => _error = e.isForbidden
          ? "You don't have permission to save suppliers."
          : e.message);
    } catch (_) {
      setState(() => _error = 'Could not reach the server.');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
          title: Text(_isEdit ? 'Edit supplier' : 'New supplier'),
          scrolledUnderElevation: 1),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          for (final f in _textFields) ...[
            LulaTextField(
              controller: _c[f.$1]!,
              label: f.$2,
              keyboardType: f.$3,
              required: f.$4,
              maxLines: f.$5 ? 3 : 1,
            ),
            const SizedBox(height: 16),
          ],
          LulaTextField(
            controller: _categories,
            label: 'Categories (comma-separated)',
          ),
          const SizedBox(height: 16),
          LulaTextField(
            controller: _bee,
            label: 'B-BBEE level (optional)',
            keyboardType: TextInputType.number,
          ),
          const SizedBox(height: 8),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Preferred supplier'),
            value: _preferred,
            onChanged: (v) => setState(() => _preferred = v),
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!, style: const TextStyle(color: kRed, fontSize: 13)),
          ],
          const SizedBox(height: 22),
          LulaButton(
            label: _isEdit ? 'Save changes' : 'Create supplier',
            loadingLabel: 'Saving…',
            loading: _saving,
            onPressed: _save,
          ),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

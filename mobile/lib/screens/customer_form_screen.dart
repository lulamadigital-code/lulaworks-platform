import 'package:flutter/material.dart';

import '../api/api_client.dart';

/// Create or edit a customer. On create the backend generates the code and seeds
/// default departments (via the create_customer service); on edit we PATCH only
/// the fields shown here.
class CustomerFormScreen extends StatefulWidget {
  const CustomerFormScreen({super.key, required this.api, this.existing});
  final ApiClient api;
  final Map<String, dynamic>? existing; // null = create

  @override
  State<CustomerFormScreen> createState() => _CustomerFormScreenState();
}

class _CustomerFormScreenState extends State<CustomerFormScreen> {
  static const _types = [
    ('', 'Not set'), ('mine', 'Mine'), ('industrial', 'Industrial / Manufacturing'),
    ('construction', 'Construction'), ('engineering', 'Engineering firm'),
    ('utility', 'Utility / Power'), ('government', 'Government / Municipal'),
    ('commercial', 'Commercial'), ('other', 'Other'),
  ];
  static const _statuses = [
    ('prospect', 'Prospect'), ('active', 'Active'), ('on_hold', 'On hold'),
    ('dormant', 'Dormant'), ('blacklisted', 'Blacklisted'),
  ];
  static const _textFields = <(String, String, TextInputType)>[
    ('name', 'Registered name *', TextInputType.text),
    ('trading_name', 'Trading name', TextInputType.text),
    ('registration_no', 'Registration number', TextInputType.text),
    ('vat_no', 'VAT number', TextInputType.text),
    ('email', 'Email', TextInputType.emailAddress),
    ('telephone', 'Telephone', TextInputType.phone),
    ('mobile', 'Mobile', TextInputType.phone),
    ('city', 'City', TextInputType.text),
    ('province', 'Province', TextInputType.text),
    ('notes', 'Notes', TextInputType.multiline),
  ];

  final _c = <String, TextEditingController>{};
  String _type = '';
  String _status = 'active';
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
    _type = '${e['customer_type'] ?? ''}';
    _status = '${e['status'] ?? 'active'}';
  }

  @override
  void dispose() {
    for (final c in _c.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    if (_c['name']!.text.trim().isEmpty) {
      setState(() => _error = 'A registered name is required.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final body = <String, dynamic>{
      for (final f in _textFields) f.$1: _c[f.$1]!.text.trim(),
      'customer_type': _type,
      'status': _status,
    };
    final messenger = ScaffoldMessenger.of(context);
    try {
      final saved = _isEdit
          ? await widget.api.patch('/customers/${widget.existing!['id']}/', body)
          : await widget.api.post('/customers/', body);
      if (!mounted) return;
      messenger.showSnackBar(
          SnackBar(content: Text(_isEdit ? 'Customer saved' : 'Customer created')));
      Navigator.of(context).pop(saved);
    } on ApiException catch (e) {
      setState(() => _error = e.isForbidden
          ? "You don't have permission to save customers."
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
      appBar: AppBar(title: Text(_isEdit ? 'Edit customer' : 'New customer')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          for (final f in _textFields) ...[
            TextField(
              controller: _c[f.$1],
              keyboardType: f.$3,
              maxLines: f.$1 == 'notes' ? 3 : 1,
              decoration: InputDecoration(
                  labelText: f.$2, border: const OutlineInputBorder()),
            ),
            const SizedBox(height: 12),
          ],
          DropdownButtonFormField<String>(
            value: _type,
            decoration: const InputDecoration(
                labelText: 'Type', border: OutlineInputBorder()),
            items: [
              for (final t in _types)
                DropdownMenuItem(value: t.$1, child: Text(t.$2)),
            ],
            onChanged: (v) => setState(() => _type = v ?? ''),
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            value: _status,
            decoration: const InputDecoration(
                labelText: 'Status', border: OutlineInputBorder()),
            items: [
              for (final s in _statuses)
                DropdownMenuItem(value: s.$1, child: Text(s.$2)),
            ],
            onChanged: (v) => setState(() => _status = v ?? 'active'),
          ),
          if (_error != null) ...[
            const SizedBox(height: 14),
            Text(_error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          const SizedBox(height: 20),
          FilledButton.icon(
            onPressed: _saving ? null : _save,
            icon: _saving
                ? const SizedBox(
                    height: 18,
                    width: 18,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.save),
            label: Text(_saving ? 'Saving…' : 'Save'),
          ),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

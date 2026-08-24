import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

/// Create or edit a customer. On create the backend generates the code and seeds
/// default departments (via create_customer); on edit we PATCH the shown fields.
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
  // (key, label, keyboard, required, multiline)
  static const _textFields = <(String, String, TextInputType, bool, bool)>[
    ('name', 'Registered name', TextInputType.text, true, false),
    ('trading_name', 'Trading name', TextInputType.text, false, false),
    ('registration_no', 'Registration number', TextInputType.text, false, false),
    ('vat_no', 'VAT number', TextInputType.text, false, false),
    ('email', 'Email', TextInputType.emailAddress, false, false),
    ('telephone', 'Telephone', TextInputType.phone, false, false),
    ('mobile', 'Mobile', TextInputType.phone, false, false),
    ('city', 'City', TextInputType.text, false, false),
    ('province', 'Province', TextInputType.text, false, false),
    ('notes', 'Notes', TextInputType.multiline, false, true),
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
    FocusScope.of(context).unfocus();
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
      appBar: AppBar(
          title: Text(_isEdit ? 'Edit customer' : 'New customer'),
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
          LulaDropdown<String>(
            label: 'Type',
            value: _type,
            items: [
              for (final t in _types)
                DropdownMenuItem(value: t.$1, child: Text(t.$2)),
            ],
            onChanged: (v) => setState(() => _type = v ?? ''),
          ),
          const SizedBox(height: 16),
          LulaDropdown<String>(
            label: 'Status',
            value: _status,
            items: [
              for (final s in _statuses)
                DropdownMenuItem(value: s.$1, child: Text(s.$2)),
            ],
            onChanged: (v) => setState(() => _status = v ?? 'active'),
          ),
          if (_error != null) ...[
            const SizedBox(height: 16),
            Text(_error!, style: const TextStyle(color: kRed, fontSize: 13)),
          ],
          const SizedBox(height: 22),
          LulaButton(
            label: _isEdit ? 'Save changes' : 'Create customer',
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

import 'package:flutter/material.dart';

import '../api/api_client.dart';

/// Company profile editor — the admin/owner surface for company.manage. Reads
/// /company/ and PATCHes the editable fields. The backend rejects the write with
/// 403 if the user lacks company.manage (we only route here when they have it).
class CompanySettingsScreen extends StatefulWidget {
  const CompanySettingsScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<CompanySettingsScreen> createState() => _CompanySettingsScreenState();
}

class _CompanySettingsScreenState extends State<CompanySettingsScreen> {
  // The editable text fields we expose, in display order.
  static const _fields = <(String, String, TextInputType)>[
    ('name', 'Registered name', TextInputType.text),
    ('trading_name', 'Trading name', TextInputType.text),
    ('registration_no', 'Registration number', TextInputType.text),
    ('vat_no', 'VAT number', TextInputType.text),
    ('city', 'City', TextInputType.text),
    ('province', 'Province', TextInputType.text),
    ('country', 'Country', TextInputType.text),
  ];

  final _controllers = <String, TextEditingController>{};
  bool _loading = true;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final c = (await widget.api.get('/company/') as Map).cast<String, dynamic>();
      for (final f in _fields) {
        _controllers[f.$1] =
            TextEditingController(text: '${c[f.$1] ?? ''}');
      }
    } catch (e) {
      _error = '$e';
    }
    if (mounted) setState(() => _loading = false);
  }

  @override
  void dispose() {
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    final body = <String, dynamic>{
      for (final f in _fields) f.$1: _controllers[f.$1]!.text.trim(),
    };
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.patch('/company/', body);
      await widget.api.refreshMe(); // keep the cached company name in sync
      if (!mounted) return;
      messenger.showSnackBar(const SnackBar(content: Text('Company saved')));
      Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      setState(() => _error = e.isForbidden
          ? "You don't have permission to edit the company."
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
      appBar: AppBar(title: const Text('Company settings')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                for (final f in _fields) ...[
                  TextField(
                    controller: _controllers[f.$1],
                    keyboardType: f.$3,
                    decoration: InputDecoration(
                        labelText: f.$2, border: const OutlineInputBorder()),
                  ),
                  const SizedBox(height: 14),
                ],
                if (_error != null) ...[
                  Text(_error!,
                      style: TextStyle(color: Theme.of(context).colorScheme.error)),
                  const SizedBox(height: 12),
                ],
                FilledButton.icon(
                  onPressed: _saving ? null : _save,
                  icon: _saving
                      ? const SizedBox(
                          height: 18,
                          width: 18,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.save),
                  label: Text(_saving ? 'Saving…' : 'Save changes'),
                ),
                const SizedBox(height: 24),
              ],
            ),
    );
  }
}

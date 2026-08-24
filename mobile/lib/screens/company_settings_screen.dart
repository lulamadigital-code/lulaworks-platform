import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

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
  static const _fields = <(String, String, bool)>[
    ('name', 'Registered name', true),
    ('trading_name', 'Trading name', false),
    ('registration_no', 'Registration number', false),
    ('vat_no', 'VAT number', false),
    ('city', 'City', false),
    ('province', 'Province', false),
    ('country', 'Country', false),
  ];

  final _c = <String, TextEditingController>{};
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
        _c[f.$1] = TextEditingController(text: '${c[f.$1] ?? ''}');
      }
    } catch (e) {
      _error = '$e';
    }
    if (mounted) setState(() => _loading = false);
  }

  @override
  void dispose() {
    for (final c in _c.values) {
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
      for (final f in _fields) f.$1: _c[f.$1]!.text.trim(),
    };
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.patch('/company/', body);
      await widget.api.refreshMe();
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
      appBar: AppBar(title: const Text('Company profile'), scrolledUnderElevation: 1),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: kBrand))
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                for (final f in _fields) ...[
                  LulaTextField(
                    controller: _c[f.$1]!,
                    label: f.$2,
                    required: f.$3,
                  ),
                  const SizedBox(height: 16),
                ],
                if (_error != null) ...[
                  Text(_error!, style: const TextStyle(color: kRed, fontSize: 13)),
                  const SizedBox(height: 12),
                ],
                const SizedBox(height: 4),
                LulaButton(
                  label: 'Save changes',
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

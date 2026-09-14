import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

/// Company profile editor — the admin/owner surface for company.manage. Reads
/// /company/ and PATCHes the editable fields. The backend rejects the write with
/// 403 if the user lacks company.manage (we only route here when they have it).
///
/// Country is the source of truth: picking it (from the shared reference list,
/// SA first) drives the currency, which the backend derives — mirroring web.
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
    ('province', 'Province / State', false),
  ];

  final _c = <String, TextEditingController>{};
  List<Map<String, dynamic>> _countries = const [];
  String? _countryCode;
  String _currency = '';
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
      _currency = '${c['currency'] ?? ''}';
      try {
        final rows = (await widget.api.get('/reference/countries/') as List)
            .map((e) => (e as Map).cast<String, dynamic>())
            .toList();
        _countries = rows;
        final code = '${c['country_code'] ?? ''}';
        if (rows.any((r) => r['code'] == code)) _countryCode = code;
      } catch (_) {
        // Reference list unavailable — the picker just shows no options; the
        // other fields still save.
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

  void _onCountry(String? code) {
    setState(() {
      _countryCode = code;
      final hit = _countries.firstWhere((r) => r['code'] == code,
          orElse: () => const {});
      _currency = '${hit['currency'] ?? _currency}';
    });
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    final body = <String, dynamic>{
      for (final f in _fields) f.$1: _c[f.$1]!.text.trim(),
      if (_countryCode != null) 'country_code': _countryCode,
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
                if (_countries.isNotEmpty) ...[
                  LulaDropdown<String>(
                    label: 'Country',
                    value: _countryCode,
                    onChanged: _onCountry,
                    items: [
                      for (final r in _countries)
                        DropdownMenuItem(
                          value: '${r['code']}',
                          child: Text('${r['flag'] ?? ''} ${r['name'] ?? ''}',
                              overflow: TextOverflow.ellipsis),
                        ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _currency.isEmpty
                        ? 'Currency is set automatically from your country.'
                        : 'Currency: $_currency — set automatically from your country.',
                    style: const TextStyle(color: kMuted, fontSize: 12.5),
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

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

/// Capture a customer PO — upload the document (the backend auto-extracts its
/// number/value/client/lines) and/or type the fields. Posts multipart to
/// /customer-pos/ (quotes.create). On success returns the created PO so the
/// caller can open it and match it to a quotation.
class CustomerPoFormScreen extends StatefulWidget {
  const CustomerPoFormScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<CustomerPoFormScreen> createState() => _CustomerPoFormScreenState();
}

class _CustomerPoFormScreenState extends State<CustomerPoFormScreen> {
  final _poNumber = TextEditingController();
  final _client = TextEditingController();
  final _site = TextEditingController();
  final _value = TextEditingController();
  final _notes = TextEditingController();
  String? _filePath;
  String? _fileName;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _poNumber.dispose();
    _client.dispose();
    _site.dispose();
    _value.dispose();
    _notes.dispose();
    super.dispose();
  }

  Future<void> _pickFile() async {
    final picked = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['pdf', 'jpg', 'jpeg', 'png'],
      withData: false,
    );
    final file = picked?.files.single;
    if (file?.path == null) return;
    setState(() {
      _filePath = file!.path;
      _fileName = file.name;
    });
  }

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    if (_filePath == null &&
        _poNumber.text.trim().isEmpty &&
        _value.text.trim().isEmpty &&
        _client.text.trim().isEmpty) {
      setState(() => _error = 'Add a PO number, or upload the PO document.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    try {
      final po = await widget.api.postMultipart(
        '/customer-pos/',
        fields: {
          'po_number': _poNumber.text.trim(),
          'client_name': _client.text.trim(),
          'site': _site.text.trim(),
          'value': _value.text.trim(),
          'notes': _notes.text.trim(),
        },
        filePath: _filePath,
        fileField: 'document',
      ) as Map<String, dynamic>;
      if (!mounted) return;
      messenger.showSnackBar(
          SnackBar(content: Text('PO ${po['po_number'] ?? ''} captured')));
      navigator.pop(po);
    } on ApiException catch (e) {
      setState(() => _error = e.isForbidden
          ? "You don't have permission to add purchase orders."
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
      appBar: AppBar(title: const Text('Add customer PO'), scrolledUnderElevation: 1),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
                color: kBrandTint,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: kLine)),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                const Icon(Icons.auto_awesome, size: 18, color: kBrandDark),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    _fileName == null
                        ? 'Upload the PO document — we read the number, value and lines.'
                        : 'Attached: $_fileName',
                    style: const TextStyle(fontSize: 13, color: kInk),
                  ),
                ),
              ]),
              const SizedBox(height: 10),
              OutlinedButton.icon(
                onPressed: _pickFile,
                icon: const Icon(Icons.upload_file, size: 18),
                label: Text(_fileName == null ? 'Choose file' : 'Replace file'),
              ),
            ]),
          ),
          const SizedBox(height: 20),
          LulaTextField(controller: _poNumber, label: 'PO number'),
          const SizedBox(height: 16),
          LulaTextField(controller: _client, label: 'Client name'),
          const SizedBox(height: 16),
          LulaTextField(controller: _site, label: 'Site (optional)'),
          const SizedBox(height: 16),
          LulaTextField(
              controller: _value,
              label: 'Value (optional)',
              keyboardType: const TextInputType.numberWithOptions(decimal: true)),
          const SizedBox(height: 16),
          LulaTextField(
              controller: _notes, label: 'Notes (optional)', maxLines: 2),
          if (_error != null) ...[
            const SizedBox(height: 16),
            Text(_error!, style: const TextStyle(color: kRed, fontSize: 13)),
          ],
          const SizedBox(height: 22),
          LulaButton(
            label: 'Capture PO',
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

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:image_picker/image_picker.dart';

import '../api/api_client.dart';
import '../api/report_store.dart';
import '../models.dart';
import '../widgets/mini_map.dart';

/// Capture a field report against a task — the heart of the Work Execution
/// System on mobile. Every report records who/when/where (GPS) and, for a
/// purchase, the money and a photo of the receipt/invoice. A material purchase
/// can scan a supplier invoice and prefill the fields for the worker to confirm.
class ReportCaptureScreen extends StatefulWidget {
  const ReportCaptureScreen({super.key, required this.api, required this.taskId});
  final ApiClient api;
  final String taskId;

  @override
  State<ReportCaptureScreen> createState() => _ReportCaptureScreenState();
}

const _kinds = <String, String>{
  'time_event': 'Time & attendance',
  'progress': 'Progress update',
  'fuel': 'Fuel purchase',
  'material': 'Material purchase',
  'meal': 'Meal / food',
  'expense': 'Other expense',
  'general': 'General / evidence',
};

const _timeEvents = <String>[
  'Departed office', 'Arrived at supplier', 'Loading materials',
  'Left supplier', 'Arrived at site', 'Started work', 'Lunch break',
  'Resumed work', 'Work completed', 'Returned to office',
];

class _ReportCaptureScreenState extends State<ReportCaptureScreen> {
  String _kind = 'time_event';
  final _title = TextEditingController();
  final _notes = TextEditingController();
  final _supplier = TextEditingController();
  final _invoiceNo = TextEditingController();
  final _amount = TextEditingController();
  final _vat = TextEditingController();
  final _litres = TextEditingController();
  final _odometer = TextEditingController();
  final _vehicle = TextEditingController();
  DateTime? _docDate;

  // Budget lines the spend can draw from (the task's monetary allocations).
  List<Map<String, dynamic>> _allocations = const [];
  String? _allocationId;

  Position? _pos;
  String? _gpsError;
  bool _locating = false;

  XFile? _photo;
  bool _submitting = false;
  bool _scanning = false;

  bool get _isFinancial =>
      _kind == 'fuel' || _kind == 'material' || _kind == 'meal' || _kind == 'expense';

  @override
  void initState() {
    super.initState();
    _captureLocation(); // grab a fix immediately — the whole point is "where"
    _loadAllocations(); // budget lines this spend can be booked against
  }

  @override
  void dispose() {
    _title.dispose();
    _notes.dispose();
    _supplier.dispose();
    _invoiceNo.dispose();
    _amount.dispose();
    _vat.dispose();
    _litres.dispose();
    _odometer.dispose();
    _vehicle.dispose();
    super.dispose();
  }

  Future<void> _loadAllocations() async {
    try {
      final body = await widget.api.get('/task-allocations/?task=${widget.taskId}');
      final rows = pageResults(body)
          .where((a) => a['is_monetary'] != false)
          .toList();
      if (mounted) setState(() => _allocations = rows);
    } catch (_) {
      // Non-fatal: capture still works without picking a budget line.
    }
  }

  String _allocationLabel(Map<String, dynamic> a) {
    final parts = <String>[
      '${a['kind_display'] ?? a['kind'] ?? 'Budget'}',
    ];
    final label = '${a['label'] ?? ''}'.trim();
    if (label.isNotEmpty) parts.add(label);
    // `remaining` is withheld for users without finance.view_money — only show
    // it when the server actually sent it.
    if (a['remaining'] != null) parts.add('R${a['remaining']} left');
    return parts.join(' · ');
  }

  Future<void> _pickDocDate() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: _docDate ?? now,
      firstDate: DateTime(now.year - 2),
      lastDate: now,
    );
    if (picked != null && mounted) setState(() => _docDate = picked);
  }

  Future<void> _captureLocation() async {
    setState(() { _locating = true; _gpsError = null; });
    try {
      if (!await Geolocator.isLocationServiceEnabled()) {
        throw 'Location services are off — turn on GPS.';
      }
      var perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.denied) {
        throw 'Location permission denied — allow it to capture where the work '
            'happened.';
      }
      if (perm == LocationPermission.deniedForever) {
        throw 'Location is blocked for this app. Enable it in Settings › Apps › '
            'Lulaworks › Permissions.';
      }
      Position? pos;
      try {
        // Time-limit the fix so a weak signal doesn't hang the screen.
        pos = await Geolocator.getCurrentPosition(
            desiredAccuracy: LocationAccuracy.high,
            timeLimit: const Duration(seconds: 15));
      } catch (_) {
        // Fall back to the last known fix rather than capturing nothing.
        pos = await Geolocator.getLastKnownPosition();
        if (pos == null) rethrow;
      }
      if (mounted) setState(() => _pos = pos);
    } catch (e) {
      if (mounted) setState(() => _gpsError = '$e');
    } finally {
      if (mounted) setState(() => _locating = false);
    }
  }

  Future<void> _pickPhoto(ImageSource source) async {
    final x = await ImagePicker().pickImage(source: source, imageQuality: 70);
    if (x != null && mounted) setState(() => _photo = x);
  }

  Future<void> _scanInvoice() async {
    final x = await ImagePicker().pickImage(
        source: ImageSource.camera, imageQuality: 80);
    if (x == null) return;
    setState(() { _scanning = true; _photo = x; });
    try {
      final data = await widget.api.postMultipart(
          '/task-reports/extract_invoice/', filePath: x.path)
          as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _supplier.text = (data['supplier'] ?? '').toString();
        _invoiceNo.text = (data['invoice_number'] ?? '').toString();
        _amount.text = (data['amount'] ?? '').toString();
        if (_title.text.isEmpty) _title.text = 'Supplier invoice';
      });
      _snack('Extracted — please review before saving.');
    } catch (e) {
      _snack('$e');
    } finally {
      if (mounted) setState(() => _scanning = false);
    }
  }

  Future<void> _submit() async {
    if (_title.text.trim().isEmpty) {
      _snack('Give the report a title.');
      return;
    }
    setState(() => _submitting = true);
    try {
      final body = <String, dynamic>{
        'task': widget.taskId,
        'kind': _kind,
        'title': _title.text.trim(),
        'notes': _notes.text.trim(),
      };
      if (_kind == 'time_event') body['event'] = _title.text.trim();
      if (_pos != null) {
        body['latitude'] = _pos!.latitude.toStringAsFixed(6);
        body['longitude'] = _pos!.longitude.toStringAsFixed(6);
        body['gps_accuracy_m'] = _pos!.accuracy.toStringAsFixed(1);
      }
      if (_isFinancial) {
        body['supplier'] = _supplier.text.trim();
        body['invoice_number'] = _invoiceNo.text.trim();
        if (_amount.text.trim().isNotEmpty) body['amount'] = _amount.text.trim();
        if (_vat.text.trim().isNotEmpty) body['vat_amount'] = _vat.text.trim();
        if (_docDate != null) {
          body['document_date'] =
              '${_docDate!.year.toString().padLeft(4, '0')}-'
              '${_docDate!.month.toString().padLeft(2, '0')}-'
              '${_docDate!.day.toString().padLeft(2, '0')}';
        }
        if (_allocationId != null) body['allocation'] = _allocationId;
      }
      if (_kind == 'fuel') {
        if (_litres.text.trim().isNotEmpty) body['litres'] = _litres.text.trim();
        if (_odometer.text.trim().isNotEmpty) {
          body['odometer_km'] = _odometer.text.trim();
        }
        if (_vehicle.text.trim().isNotEmpty) body['vehicle'] = _vehicle.text.trim();
      }
      try {
        final report = await widget.api.post('/task-reports/', body)
            as Map<String, dynamic>;
        if (_photo != null) {
          await widget.api.postMultipart(
              '/task-reports/${report['id']}/photo/', filePath: _photo!.path);
        }
      } catch (e) {
        // A real server rejection (permission/validation) should surface; a
        // connectivity failure means "no signal" → queue it to sync later.
        if (e is ApiException) rethrow;
        await ReportStore().enqueue(body, photoPath: _photo?.path);
        if (!mounted) return;
        Navigator.pop(context, 'offline');
        return;
      }
      if (!mounted) return;
      Navigator.pop(context, true);
    } on ApiException catch (e) {
      _snack(e.message);
    } catch (e) {
      _snack('$e');
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  void _snack(String m) => ScaffoldMessenger.of(context)
      .showSnackBar(SnackBar(content: Text(m)));

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('New report')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        DropdownButtonFormField<String>(
          value: _kind,
          decoration: const InputDecoration(labelText: 'Report type'),
          items: _kinds.entries
              .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value)))
              .toList(),
          onChanged: (v) => setState(() => _kind = v ?? _kind),
        ),
        const SizedBox(height: 12),
        if (_kind == 'time_event')
          Wrap(spacing: 6, runSpacing: 6, children: [
            for (final e in _timeEvents)
              ActionChip(label: Text(e), onPressed: () => _title.text = e),
          ]),
        if (_kind == 'time_event') const SizedBox(height: 12),
        TextField(
          controller: _title,
          decoration: InputDecoration(
              labelText: _kind == 'time_event' ? 'Event' : 'Title'),
        ),
        if (_kind == 'material') ...[
          const SizedBox(height: 12),
          OutlinedButton.icon(
            onPressed: _scanning ? null : _scanInvoice,
            icon: _scanning
                ? const SizedBox(
                    width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.document_scanner),
            label: const Text('Scan supplier invoice'),
          ),
        ],
        if (_isFinancial) ...[
          const SizedBox(height: 12),
          TextField(controller: _supplier,
              decoration: const InputDecoration(labelText: 'Supplier')),
          const SizedBox(height: 12),
          TextField(controller: _invoiceNo,
              decoration: const InputDecoration(labelText: 'Invoice / ref')),
          const SizedBox(height: 12),
          // Core capture: amount + litres side by side (fuel). For material/expense
          // the amount takes the full width (no litres).
          Row(children: [
            Expanded(
              child: TextField(controller: _amount,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: const InputDecoration(labelText: 'Amount (ZAR)')),
            ),
            if (_kind == 'fuel') ...[
              const SizedBox(width: 12),
              Expanded(
                child: TextField(controller: _litres,
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                    decoration: const InputDecoration(labelText: 'Litres')),
              ),
            ],
          ]),
          // Everything else is optional — tucked away so the quick field capture
          // stays photo + supplier + amount (+ litres for fuel).
          Theme(
            data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
            child: ExpansionTile(
              tilePadding: EdgeInsets.zero,
              childrenPadding: const EdgeInsets.only(bottom: 8),
              title: const Text('More detail (optional)',
                  style: TextStyle(fontSize: 14)),
              children: [
                Row(children: [
                  Expanded(
                    child: TextField(controller: _vat,
                        keyboardType: const TextInputType.numberWithOptions(decimal: true),
                        decoration: const InputDecoration(labelText: 'VAT (ZAR)')),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: InkWell(
                      onTap: _pickDocDate,
                      child: InputDecorator(
                        decoration: const InputDecoration(labelText: 'Receipt date'),
                        child: Text(_docDate == null
                            ? 'Tap to set'
                            : '${_docDate!.year}-${_docDate!.month.toString().padLeft(2, '0')}-${_docDate!.day.toString().padLeft(2, '0')}'),
                      ),
                    ),
                  ),
                ]),
                if (_kind == 'fuel') ...[
                  const SizedBox(height: 12),
                  Row(children: [
                    Expanded(
                      child: TextField(controller: _odometer,
                          keyboardType: const TextInputType.numberWithOptions(decimal: true),
                          decoration: const InputDecoration(labelText: 'Odometer (km)')),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: TextField(controller: _vehicle,
                          decoration: const InputDecoration(labelText: 'Vehicle')),
                    ),
                  ]),
                ],
                if (_allocations.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String?>(
                    value: _allocationId,
                    isExpanded: true,
                    decoration: const InputDecoration(labelText: 'Draw from budget'),
                    items: [
                      const DropdownMenuItem<String?>(
                          value: null, child: Text('— none —')),
                      for (final a in _allocations)
                        DropdownMenuItem<String?>(
                          value: '${a['id']}',
                          child: Text(_allocationLabel(a),
                              overflow: TextOverflow.ellipsis),
                        ),
                    ],
                    onChanged: (v) => setState(() => _allocationId = v),
                  ),
                ],
              ],
            ),
          ),
        ],
        const SizedBox(height: 12),
        TextField(controller: _notes, minLines: 2, maxLines: 4,
            decoration: const InputDecoration(labelText: 'Notes')),
        const SizedBox(height: 16),
        _LocationCard(
          pos: _pos, locating: _locating, error: _gpsError,
          onRetry: _captureLocation,
        ),
        const SizedBox(height: 12),
        if (_isFinancial)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(children: [
              Icon(_photo != null ? Icons.check_circle : Icons.receipt_long,
                  size: 18,
                  color: _photo != null
                      ? Colors.green
                      : Theme.of(context).colorScheme.outline),
              const SizedBox(width: 6),
              Text(_photo != null ? 'Receipt photo attached' : 'Photograph the receipt',
                  style: Theme.of(context).textTheme.bodyMedium),
            ]),
          ),
        Row(children: [
          Expanded(
            child: OutlinedButton.icon(
              onPressed: () => _pickPhoto(ImageSource.camera),
              icon: const Icon(Icons.camera_alt), label: const Text('Camera'),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: OutlinedButton.icon(
              onPressed: () => _pickPhoto(ImageSource.gallery),
              icon: const Icon(Icons.photo_library), label: const Text('Gallery'),
            ),
          ),
        ]),
        if (_photo != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text('Attached: ${_photo!.name}',
                style: Theme.of(context).textTheme.bodySmall),
          ),
        const SizedBox(height: 24),
        FilledButton(
          onPressed: _submitting ? null : _submit,
          child: _submitting
              ? const SizedBox(
                  height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
              : const Text('Save report'),
        ),
      ]),
    );
  }
}

class _LocationCard extends StatelessWidget {
  const _LocationCard(
      {required this.pos, required this.locating, required this.error, required this.onRetry});
  final Position? pos;
  final bool locating;
  final String? error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      child: Column(children: [
        ListTile(
          leading: Icon(pos != null ? Icons.location_on : Icons.location_off,
              color: pos != null ? Colors.green : scheme.outline),
          title: locating
              ? const Text('Getting your location…')
              : pos != null
                  ? Text('${pos!.latitude.toStringAsFixed(5)}, '
                      '${pos!.longitude.toStringAsFixed(5)}')
                  : Text(error ?? 'No location'),
          subtitle: pos != null
              ? Text('Accuracy ±${pos!.accuracy.toStringAsFixed(0)} m')
              : null,
          trailing: IconButton(icon: const Icon(Icons.refresh), onPressed: onRetry),
        ),
        if (pos != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
            child: MiniMap(lat: pos!.latitude, lng: pos!.longitude, height: 140),
          ),
      ]),
    );
  }
}

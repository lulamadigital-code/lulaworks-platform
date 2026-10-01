import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';
import '../widgets/status_pill.dart';
import 'quotations_screen.dart';

/// RFQs — the request-for-quote intelligence pipeline, mirrored from the web:
/// upload a PDF → deterministic extraction → review the fields/lines → approve
/// (→ Quotation). The backend owns extraction and the human-approval boundary;
/// the app just drives it. Upload is gated on rfq.upload, approve on rfq.approve.
class RfqScreen extends StatefulWidget {
  const RfqScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<RfqScreen> createState() => _RfqScreenState();
}

class _RfqScreenState extends State<RfqScreen> {
  late Future<List<Map<String, dynamic>>> _future = _load();
  bool _uploading = false;

  Future<List<Map<String, dynamic>>> _load() async =>
      pageResults(await widget.api.get('/rfqs/'));

  void _showCreateOptions() {
    showModalBottomSheet<void>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: const Icon(Icons.picture_as_pdf_outlined),
            title: const Text('Upload a PDF'),
            subtitle: const Text('Client RFQ document'),
            onTap: () {
              Navigator.pop(ctx);
              _upload();
            },
          ),
          ListTile(
            leading: const Icon(Icons.content_paste),
            title: const Text('Paste RFQ text'),
            subtitle: const Text('From an email or WhatsApp message'),
            onTap: () {
              Navigator.pop(ctx);
              _pasteText();
            },
          ),
        ]),
      ),
    );
  }

  Future<void> _pasteText() async {
    final rfq = await Navigator.of(context).push<Map<String, dynamic>>(
        MaterialPageRoute(builder: (_) => _RfqTextEntry(api: widget.api)));
    if (rfq == null || !mounted) return;
    await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => _RfqDetail(api: widget.api, rfq: rfq)));
    if (mounted) setState(() => _future = _load());
  }

  Future<void> _upload() async {
    final picked = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['pdf'],
      withData: false,
    );
    final path = picked?.files.single.path;
    if (path == null || !mounted) return; // cancelled / left the screen
    setState(() => _uploading = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final rfq = await widget.api
          .postMultipart('/rfqs/', filePath: path) as Map<String, dynamic>;
      if (!mounted) return;
      messenger.showSnackBar(
          const SnackBar(content: Text('RFQ uploaded — review the extraction')));
      await Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => _RfqDetail(api: widget.api, rfq: rfq)));
      if (mounted) setState(() => _future = _load());
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission to upload RFQs."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not upload the RFQ.')));
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final canUpload = widget.api.can('rfq.upload');
    return Scaffold(
      appBar: AppBar(title: const Text('RFQs')),
      floatingActionButton: canUpload
          ? FloatingActionButton.extended(
              onPressed: _uploading ? null : _showCreateOptions,
              icon: _uploading
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Colors.white))
                  : const Icon(Icons.add),
              label: Text(_uploading ? 'Uploading…' : 'New RFQ'))
          : null,
      body: RefreshIndicator(
        onRefresh: () async => setState(() { _future = _load(); }),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 100),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
              ]);
            }
            final rows = snap.data ?? const [];
            if (rows.isEmpty) {
              return ListView(children: [
                const SizedBox(height: 120),
                Center(
                    child: Text(canUpload
                        ? 'No RFQs yet — upload one to get started.'
                        : 'No RFQs yet.')),
              ]);
            }
            return ListView.separated(
              itemCount: rows.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final r = rows[i];
                return ListTile(
                  leading: const Icon(Icons.description_outlined),
                  title: Text('${r['original_name'] ?? r['doc_class'] ?? 'RFQ'}',
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  subtitle: Text('${r['doc_class'] ?? ''}'
                      '${r['quotation_number'] != null ? ' · ${r['quotation_number']}' : ''}'),
                  trailing: StatusPill(status: '${r['status']}'),
                  onTap: () async {
                    await Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => _RfqDetail(api: widget.api, rfq: r)));
                    if (mounted) setState(() => _future = _load());
                  },
                );
              },
            );
          },
        ),
      ),
    );
  }
}

/// Review + approve one RFQ. Extracted fields (approved_value) and line items
/// are editable until the RFQ is approved; Save PATCHes /rfqs/{id}/review/,
/// Approve POSTs /rfqs/{id}/approve/ and hands off to the created quotation.
class _RfqDetail extends StatefulWidget {
  const _RfqDetail({required this.api, required this.rfq});
  final ApiClient api;
  final Map<String, dynamic> rfq;

  @override
  State<_RfqDetail> createState() => _RfqDetailState();
}

class _RfqDetailState extends State<_RfqDetail> {
  late Map<String, dynamic> _rfq = widget.rfq;
  final _fieldCtrls = <String, TextEditingController>{}; // field id -> approved
  final _lineCtrls = <String, Map<String, TextEditingController>>{};
  bool _saving = false;
  bool _approving = false;

  bool get _isApproved => '${_rfq['status']}' == 'approved';
  bool get _canReview => widget.api.can('rfq.upload') && !_isApproved;

  @override
  void initState() {
    super.initState();
    _bind(_rfq);
  }

  void _bind(Map<String, dynamic> rfq) {
    for (final c in _fieldCtrls.values) {
      c.dispose();
    }
    for (final m in _lineCtrls.values) {
      for (final c in m.values) {
        c.dispose();
      }
    }
    _fieldCtrls.clear();
    _lineCtrls.clear();
    for (final f in (rfq['fields'] as List?)?.cast<Map<String, dynamic>>() ??
        const []) {
      final id = '${f['id']}';
      _fieldCtrls[id] = TextEditingController(
          text: '${f['approved_value'] ?? f['value'] ?? ''}');
    }
    for (final l in (rfq['lines'] as List?)?.cast<Map<String, dynamic>>() ??
        const []) {
      final id = '${l['id']}';
      _lineCtrls[id] = {
        'description': TextEditingController(text: '${l['description'] ?? ''}'),
        'qty': TextEditingController(text: '${l['qty'] ?? ''}'),
        'unit': TextEditingController(text: '${l['unit'] ?? ''}'),
        'unit_price': TextEditingController(text: '${l['unit_price'] ?? ''}'),
      };
    }
  }

  @override
  void dispose() {
    for (final c in _fieldCtrls.values) {
      c.dispose();
    }
    for (final m in _lineCtrls.values) {
      for (final c in m.values) {
        c.dispose();
      }
    }
    super.dispose();
  }

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    setState(() => _saving = true);
    final messenger = ScaffoldMessenger.of(context);
    final body = {
      'fields': [
        for (final e in _fieldCtrls.entries)
          {'id': e.key, 'approved_value': e.value.text.trim()},
      ],
      'lines': [
        for (final e in _lineCtrls.entries)
          {
            'id': e.key,
            'description': e.value['description']!.text.trim(),
            'qty': e.value['qty']!.text.trim(),
            'unit': e.value['unit']!.text.trim(),
            'unit_price': e.value['unit_price']!.text.trim(),
          },
      ],
    };
    try {
      final fresh = await widget.api.patch('/rfqs/${_rfq['id']}/review/', body)
          as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _rfq = fresh;
        _bind(fresh);
      });
      messenger.showSnackBar(const SnackBar(content: Text('Review saved')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  String _guessClientName() {
    for (final f in (_rfq['fields'] as List?)?.cast<Map<String, dynamic>>() ??
        const []) {
      final key = '${f['key']}'.toLowerCase();
      if (key.contains('client') || key.contains('customer') ||
          key.contains('company') || key.contains('buyer')) {
        final ctrl = _fieldCtrls['${f['id']}'];
        final v = ctrl?.text.trim() ?? '${f['approved_value'] ?? f['value'] ?? ''}';
        if (v.isNotEmpty) return v;
      }
    }
    return '';
  }

  Future<void> _approve() async {
    final ctrl = TextEditingController(text: _guessClientName());
    final clientName = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Approve RFQ'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          const Text('This creates a quotation from the reviewed RFQ. '
              'Confirm the client name:'),
          const SizedBox(height: 12),
          TextField(
            controller: ctrl,
            autofocus: true,
            decoration: const InputDecoration(labelText: 'Client name'),
          ),
        ]),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
              child: const Text('Approve')),
        ],
      ),
    );
    if (clientName == null || clientName.isEmpty || !mounted) return;
    setState(() => _approving = true);
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    try {
      final res = await widget.api.post(
          '/rfqs/${_rfq['id']}/approve/', {'client_name': clientName}) as Map;
      final quotation = (res['quotation'] as Map?)?.cast<String, dynamic>();
      if (!mounted) return;
      messenger.showSnackBar(SnackBar(
          content: Text(
              'Quotation ${quotation?['number'] ?? ''} created from RFQ')));
      if (quotation != null && quotation['id'] != null) {
        await navigator.pushReplacement(MaterialPageRoute(
            builder: (_) => QuotationDetailScreen(
                api: widget.api, quoteId: '${quotation['id']}')));
      } else {
        navigator.pop();
      }
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission to approve RFQs."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    } finally {
      if (mounted) setState(() => _approving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final fields =
        (_rfq['fields'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    final lines =
        (_rfq['lines'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    final warnings = (_rfq['warnings'] as List?) ?? const [];
    final canApprove = widget.api.can('rfq.approve') && !_isApproved;
    return Scaffold(
      appBar: AppBar(title: Text('${_rfq['original_name'] ?? 'RFQ'}')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Row(children: [
          StatusPill(status: '${_rfq['status']}'),
          const Spacer(),
          if ('${_rfq['quotation_number'] ?? ''}'.isNotEmpty)
            Text('Quote ${_rfq['quotation_number']}',
                style: Theme.of(context).textTheme.bodySmall),
        ]),
        if (warnings.isNotEmpty) ...[
          const SizedBox(height: 12),
          for (final w in warnings)
            Row(children: [
              const Icon(Icons.warning_amber, size: 16, color: Colors.orange),
              const SizedBox(width: 6),
              Expanded(
                  child: Text('$w',
                      style: Theme.of(context).textTheme.bodySmall)),
            ]),
        ],
        const SizedBox(height: 16),
        if (fields.isNotEmpty) ...[
          Text(_canReview ? 'Extracted fields (editable)' : 'Extracted fields',
              style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 8),
          for (final f in fields) _fieldRow(f),
          const SizedBox(height: 8),
        ],
        if (lines.isNotEmpty) ...[
          Text('Lines (${lines.length})',
              style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 8),
          for (final l in lines) _lineRow(l),
        ],
        const SizedBox(height: 20),
        if (_canReview)
          LulaButton(
              label: 'Save review',
              loadingLabel: 'Saving…',
              loading: _saving,
              onPressed: _save),
        if (canApprove) ...[
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed: _approving ? null : _approve,
            icon: _approving
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(
                        strokeWidth: 2, color: Colors.white))
                : const Icon(Icons.check_circle_outline),
            label: Text(_approving ? 'Approving…' : 'Approve → create quotation'),
          ),
        ],
        const SizedBox(height: 24),
      ]),
    );
  }

  Widget _fieldRow(Map<String, dynamic> f) {
    final id = '${f['id']}';
    final label = '${f['key'] ?? ''}';
    if (!_canReview) {
      return ListTile(
        dense: true,
        contentPadding: EdgeInsets.zero,
        title: Text(label),
        trailing: Flexible(
            child: Text('${f['approved_value'] ?? f['value'] ?? ''}',
                textAlign: TextAlign.right, overflow: TextOverflow.ellipsis)),
      );
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: LulaTextField(controller: _fieldCtrls[id]!, label: label),
    );
  }

  Widget _lineRow(Map<String, dynamic> l) {
    final id = '${l['id']}';
    if (!_canReview) {
      return Card(
        margin: const EdgeInsets.only(bottom: 8),
        child: ListTile(
          dense: true,
          title: Text('${l['description'] ?? '—'}'),
          subtitle: Text('Qty ${l['qty'] ?? '—'}'
              '${l['unit'] != null ? ' ${l['unit']}' : ''}'),
        ),
      );
    }
    final m = _lineCtrls[id]!;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
          border: Border.all(color: kLine),
          borderRadius: BorderRadius.circular(12)),
      child: Column(children: [
        LulaTextField(controller: m['description']!, label: 'Description'),
        const SizedBox(height: 10),
        Row(children: [
          Expanded(
              child: LulaTextField(
                  controller: m['qty']!,
                  label: 'Qty',
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true))),
          const SizedBox(width: 10),
          Expanded(child: LulaTextField(controller: m['unit']!, label: 'Unit')),
          const SizedBox(width: 10),
          Expanded(
              child: LulaTextField(
                  controller: m['unit_price']!,
                  label: 'Unit price',
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true))),
        ]),
      ]),
    );
  }
}

/// Paste an RFQ that arrived as text (email body / WhatsApp). Posts to
/// /rfqs/from-text/ (gated rfq.upload); the backend runs the same extraction and
/// returns a reviewable RFQ, which the caller opens in _RfqDetail.
class _RfqTextEntry extends StatefulWidget {
  const _RfqTextEntry({required this.api});
  final ApiClient api;

  @override
  State<_RfqTextEntry> createState() => _RfqTextEntryState();
}

class _RfqTextEntryState extends State<_RfqTextEntry> {
  final _text = TextEditingController();
  final _name = TextEditingController();
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _text.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    if (_text.text.trim().isEmpty) {
      setState(() => _error = 'Paste the RFQ text first.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final navigator = Navigator.of(context);
    try {
      final rfq = await widget.api.post('/rfqs/from-text/', {
        'text': _text.text.trim(),
        'original_name': _name.text.trim(),
      }) as Map<String, dynamic>;
      if (!mounted) return;
      navigator.pop(rfq);
    } on ApiException catch (e) {
      setState(() => _error = e.isForbidden
          ? "You don't have permission to create RFQs."
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
      appBar: AppBar(title: const Text('Paste RFQ'), scrolledUnderElevation: 1),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          LulaTextField(
            controller: _name,
            label: 'Reference / name (optional)',
          ),
          const SizedBox(height: 16),
          const Text('RFQ text',
              style: TextStyle(fontWeight: FontWeight.w700, color: kInk)),
          const SizedBox(height: 8),
          const Text(
            'Paste the email body or message. The backend extracts the client, '
            'items and quantities for you to review before approving.',
            style: TextStyle(color: kMuted, fontSize: 13),
          ),
          const SizedBox(height: 12),
          LulaTextField(
            controller: _text,
            label: 'Paste here',
            maxLines: 12,
          ),
          if (_error != null) ...[
            const SizedBox(height: 16),
            Text(_error!, style: const TextStyle(color: kRed, fontSize: 13)),
          ],
          const SizedBox(height: 22),
          LulaButton(
            label: 'Extract RFQ',
            loadingLabel: 'Extracting…',
            loading: _saving,
            onPressed: _save,
          ),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

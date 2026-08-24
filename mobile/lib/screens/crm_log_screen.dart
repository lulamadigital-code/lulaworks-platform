import 'package:flutter/material.dart';

import '../api/api_client.dart';

/// Log a CRM entry against a customer — a call/meeting/WhatsApp that happened
/// (Interaction), a note, or a follow-up to do later (Activity). Thin wrapper
/// over the backend's CRM services; returns true on success.
class CrmLogScreen extends StatefulWidget {
  const CrmLogScreen({super.key, required this.api, required this.customerId});
  final ApiClient api;
  final String customerId;

  @override
  State<CrmLogScreen> createState() => _CrmLogScreenState();
}

enum _Kind { call, meeting, whatsapp, note, followUp }

class _CrmLogScreenState extends State<CrmLogScreen> {
  _Kind _kind = _Kind.call;
  final _text = TextEditingController();
  DateTime? _due;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  String get _label => switch (_kind) {
        _Kind.call => 'What was discussed',
        _Kind.meeting => 'Meeting notes',
        _Kind.whatsapp => 'Message summary',
        _Kind.note => 'Note',
        _Kind.followUp => 'What needs doing',
      };

  Future<void> _save() async {
    if (_text.text.trim().isEmpty) {
      setState(() => _error = 'Add a few words first.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final id = widget.customerId;
    final api = widget.api;
    final messenger = ScaffoldMessenger.of(context);
    try {
      switch (_kind) {
        case _Kind.call:
        case _Kind.meeting:
        case _Kind.whatsapp:
          await api.post('/customers/$id/log-interaction/', {
            'summary': _text.text.trim(),
            'channel': _kind == _Kind.call
                ? 'phone'
                : _kind == _Kind.meeting
                    ? 'meeting'
                    : 'whatsapp',
            'direction': 'out',
          });
        case _Kind.note:
          await api.post('/customers/$id/add-note/', {'body': _text.text.trim()});
        case _Kind.followUp:
          await api.post('/customers/$id/schedule-activity/', {
            'subject': _text.text.trim(),
            'activity_type': 'follow_up',
            if (_due != null) 'due_at': _due!.toUtc().toIso8601String(),
          });
      }
      if (!mounted) return;
      messenger.showSnackBar(const SnackBar(content: Text('Logged')));
      Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      setState(() => _error = e.isForbidden
          ? "You don't have permission to log CRM activity."
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
      appBar: AppBar(title: const Text('Log activity')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _chip(_Kind.call, 'Call', Icons.phone),
              _chip(_Kind.meeting, 'Meeting', Icons.groups),
              _chip(_Kind.whatsapp, 'WhatsApp', Icons.chat),
              _chip(_Kind.note, 'Note', Icons.sticky_note_2),
              _chip(_Kind.followUp, 'Follow-up', Icons.event),
            ],
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _text,
            maxLines: 4,
            autofocus: true,
            decoration: InputDecoration(
                labelText: _label, border: const OutlineInputBorder()),
          ),
          if (_kind == _Kind.followUp) ...[
            const SizedBox(height: 12),
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.schedule),
              title: Text(_due == null
                  ? 'Set a due date (optional)'
                  : 'Due ${_due!.day}/${_due!.month}/${_due!.year}'),
              trailing: _due != null
                  ? IconButton(
                      icon: const Icon(Icons.clear),
                      onPressed: () => setState(() => _due = null))
                  : null,
              onTap: () async {
                final now = DateTime.now();
                final picked = await showDatePicker(
                  context: context,
                  initialDate: now.add(const Duration(days: 1)),
                  firstDate: now,
                  lastDate: now.add(const Duration(days: 365)),
                );
                if (picked != null) setState(() => _due = picked);
              },
            ),
          ],
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
            label: Text(_saving ? 'Saving…' : 'Log it'),
          ),
        ],
      ),
    );
  }

  Widget _chip(_Kind kind, String label, IconData icon) {
    final selected = _kind == kind;
    return ChoiceChip(
      avatar: Icon(icon,
          size: 18,
          color: selected ? Theme.of(context).colorScheme.onPrimary : null),
      label: Text(label),
      selected: selected,
      onSelected: (_) => setState(() => _kind = kind),
    );
  }
}

import 'package:flutter/material.dart';

import '../api/api_client.dart';

/// Change the signed-in user's own password. Requires the current password;
/// the backend validates strength and rejects a wrong current password.
class ChangePasswordScreen extends StatefulWidget {
  const ChangePasswordScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<ChangePasswordScreen> createState() => _ChangePasswordScreenState();
}

class _ChangePasswordScreenState extends State<ChangePasswordScreen> {
  final _current = TextEditingController();
  final _next = TextEditingController();
  final _confirm = TextEditingController();
  bool _busy = false;
  bool _obscure = true;
  String? _error;

  @override
  void dispose() {
    for (final c in [_current, _next, _confirm]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _submit() async {
    if (_next.text != _confirm.text) {
      setState(() => _error = "The new passwords don't match.");
      return;
    }
    if (_next.text.length < 8) {
      setState(() => _error = 'Use at least 8 characters.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('/me/change-password/', {
        'old_password': _current.text,
        'new_password': _next.text,
      });
      if (!mounted) return;
      messenger.showSnackBar(const SnackBar(content: Text('Password changed')));
      Navigator.of(context).pop();
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Could not reach the server.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Change password')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          _pwField(_current, 'Current password'),
          _pwField(_next, 'New password'),
          _pwField(_confirm, 'Confirm new password'),
          Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              onPressed: () => setState(() => _obscure = !_obscure),
              icon: Icon(_obscure ? Icons.visibility : Icons.visibility_off, size: 18),
              label: Text(_obscure ? 'Show' : 'Hide'),
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          const SizedBox(height: 18),
          FilledButton.icon(
            onPressed: _busy ? null : _submit,
            icon: _busy
                ? const SizedBox(
                    width: 18, height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.lock_reset),
            label: Text(_busy ? 'Saving…' : 'Update password'),
          ),
        ],
      ),
    );
  }

  Widget _pwField(TextEditingController c, String label) => Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: TextField(
          controller: c,
          obscureText: _obscure,
          decoration:
              InputDecoration(labelText: label, border: const OutlineInputBorder()),
        ),
      );
}

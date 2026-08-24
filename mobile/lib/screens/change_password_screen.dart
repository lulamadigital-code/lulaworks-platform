import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

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
    FocusScope.of(context).unfocus();
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
    Widget eye() => IconButton(
          icon: Icon(_obscure ? Icons.visibility_outlined : Icons.visibility_off_outlined,
              size: 20, color: kMuted),
          onPressed: () => setState(() => _obscure = !_obscure),
        );
    return Scaffold(
      appBar: AppBar(title: const Text('Change password'), scrolledUnderElevation: 1),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          LulaTextField(
              controller: _current,
              label: 'Current password',
              obscureText: _obscure,
              required: true,
              suffix: eye()),
          const SizedBox(height: 16),
          LulaTextField(
              controller: _next,
              label: 'New password',
              obscureText: _obscure,
              required: true,
              suffix: eye()),
          const SizedBox(height: 16),
          LulaTextField(
              controller: _confirm,
              label: 'Confirm new password',
              obscureText: _obscure,
              required: true,
              onSubmitted: (_) => _submit()),
          if (_error != null) ...[
            const SizedBox(height: 14),
            Text(_error!, style: const TextStyle(color: kRed, fontSize: 13)),
          ],
          const SizedBox(height: 22),
          LulaButton(
            label: 'Update password',
            loadingLabel: 'Saving…',
            loading: _busy,
            onPressed: _submit,
          ),
        ],
      ),
    );
  }
}

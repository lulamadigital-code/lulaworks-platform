import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../widgets/brand_logo.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.api, required this.onSignedIn});
  final ApiClient api;
  final VoidCallback onSignedIn;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  late final _origin = TextEditingController(text: widget.api.origin);
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _busy = false;
  bool _showServer = false; // hidden by default; long-press the logo to reveal
  String? _error;

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.setOrigin(_origin.text);
      await widget.api.login(_email.text.trim(), _password.text);
      widget.onSignedIn();
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Cannot reach the server. Check the address.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 420),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                GestureDetector(
                  // Discreet developer affordance: long-press the logo to reveal
                  // the Server field (for pointing at a local backend). End users
                  // never see it — the app defaults to production.
                  onLongPress: () => setState(() => _showServer = !_showServer),
                  child: const BrandLogo(height: 62),
                ),
                const SizedBox(height: 10),
                Text('Contractor Operating System', textAlign: TextAlign.center,
                    style: theme.textTheme.bodyMedium
                        ?.copyWith(color: theme.colorScheme.outline)),
                const SizedBox(height: 32),
                if (_showServer) ...[
                  TextField(
                    controller: _origin,
                    decoration: const InputDecoration(
                        labelText: 'Server', prefixIcon: Icon(Icons.dns_outlined)),
                    keyboardType: TextInputType.url,
                  ),
                  const SizedBox(height: 12),
                ],
                TextField(
                  controller: _email,
                  decoration: const InputDecoration(
                      labelText: 'Email', prefixIcon: Icon(Icons.email_outlined)),
                  keyboardType: TextInputType.emailAddress,
                  autofillHints: const [AutofillHints.username],
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _password,
                  decoration: const InputDecoration(
                      labelText: 'Password', prefixIcon: Icon(Icons.lock_outline)),
                  obscureText: true,
                  onSubmitted: (_) => _submit(),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 16),
                  Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
                ],
                const SizedBox(height: 24),
                FilledButton(
                  onPressed: _busy ? null : _submit,
                  child: _busy
                      ? const SizedBox(
                          height: 20, width: 20,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Text('Sign in'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

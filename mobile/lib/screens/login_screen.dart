import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../widgets/brand_logo.dart';
import '../widgets/lula_ui.dart';

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
  bool _obscure = true;
  bool _showServer = false;
  String? _emailError;
  String? _pwError;
  String? _formError;

  static final _emailRe = RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$');

  bool _validate() {
    setState(() {
      _emailError = _email.text.trim().isEmpty
          ? 'Please enter your email address.'
          : (!_emailRe.hasMatch(_email.text.trim())
              ? 'Please enter a valid email address.'
              : null);
      _pwError =
          _password.text.isEmpty ? 'Please enter your password.' : null;
    });
    return _emailError == null && _pwError == null;
  }

  Future<void> _submit() async {
    FocusScope.of(context).unfocus();
    if (!_validate()) return;
    setState(() {
      _busy = true;
      _formError = null;
    });
    try {
      await widget.api.setOrigin(_origin.text);
      await widget.api.login(_email.text.trim(), _password.text);
      widget.onSignedIn();
    } on ApiException catch (e) {
      setState(() => _formError = _mapError(e.statusCode));
    } catch (_) {
      setState(() => _formError =
          "We couldn't connect to Lulaworks. Check your connection and try again.");
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _mapError(int code) {
    if (code == 401 || code == 400) return 'Email or password is incorrect.';
    if (code == 403) return 'This account cannot sign in. Contact your administrator.';
    if (code == 429) return 'Too many attempts. Please wait a moment and try again.';
    if (code >= 500) return 'Lulaworks is temporarily unavailable. Please try again.';
    return 'Sign in failed. Please try again.';
  }

  Future<void> _open(String url) async {
    final uri = Uri.parse(url);
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
  }

  void _forgot() {
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Reset your password'),
        content: const Text(
            'Password resets are handled by your company administrator. '
            'You can also email support@lulaworks.com and we will help you get '
            'back in.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Close')),
          FilledButton(
            onPressed: () {
              Navigator.pop(ctx);
              _open('mailto:support@lulaworks.com?subject=Password%20reset');
            },
            child: const Text('Email support'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: kBg,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 400),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Center(
                    child: GestureDetector(
                      onLongPress: () =>
                          setState(() => _showServer = !_showServer),
                      child: const BrandLogo(height: 54),
                    ),
                  ),
                  const SizedBox(height: 28),
                  const Text('Welcome back',
                      textAlign: TextAlign.center,
                      style: TextStyle(
                          fontSize: 24, fontWeight: FontWeight.w700, color: kInk,
                          letterSpacing: -0.4)),
                  const SizedBox(height: 4),
                  const Text('Sign in to your Lulaworks account',
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 14, color: kMuted)),
                  const SizedBox(height: 28),

                  // The card holding the form.
                  Container(
                    decoration: BoxDecoration(
                        color: Colors.white,
                        borderRadius: BorderRadius.circular(16),
                        border: Border.all(color: kLine)),
                    padding: const EdgeInsets.all(20),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        if (_showServer) ...[
                          LulaTextField(
                            controller: _origin,
                            label: 'Server',
                            keyboardType: TextInputType.url,
                          ),
                          const SizedBox(height: 16),
                        ],
                        LulaTextField(
                          controller: _email,
                          label: 'Email',
                          hint: 'you@company.com',
                          keyboardType: TextInputType.emailAddress,
                          autofillHints: const [AutofillHints.username, AutofillHints.email],
                          textInputAction: TextInputAction.next,
                          errorText: _emailError,
                          onChanged: (_) {
                            if (_emailError != null) setState(() => _emailError = null);
                          },
                        ),
                        const SizedBox(height: 16),
                        LulaTextField(
                          controller: _password,
                          label: 'Password',
                          obscureText: _obscure,
                          autofillHints: const [AutofillHints.password],
                          textInputAction: TextInputAction.done,
                          errorText: _pwError,
                          onChanged: (_) {
                            if (_pwError != null) setState(() => _pwError = null);
                          },
                          onSubmitted: (_) => _submit(),
                          suffix: IconButton(
                            icon: Icon(
                                _obscure
                                    ? Icons.visibility_outlined
                                    : Icons.visibility_off_outlined,
                                size: 20, color: kMuted),
                            onPressed: () => setState(() => _obscure = !_obscure),
                          ),
                        ),
                        Align(
                          alignment: Alignment.centerRight,
                          child: TextButton(
                            onPressed: _forgot,
                            style: TextButton.styleFrom(
                                foregroundColor: kBrandDark,
                                padding: const EdgeInsets.symmetric(vertical: 8)),
                            child: const Text('Forgot password?',
                                style: TextStyle(fontSize: 13)),
                          ),
                        ),
                        if (_formError != null) ...[
                          Container(
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(
                                color: kRed.withOpacity(0.08),
                                borderRadius: BorderRadius.circular(10)),
                            child: Row(children: [
                              const Icon(Icons.error_outline, size: 18, color: kRed),
                              const SizedBox(width: 8),
                              Expanded(
                                  child: Text(_formError!,
                                      style: const TextStyle(
                                          fontSize: 13, color: kRed))),
                            ]),
                          ),
                          const SizedBox(height: 14),
                        ] else
                          const SizedBox(height: 6),
                        LulaButton(
                          label: 'Sign in',
                          loadingLabel: 'Signing in…',
                          loading: _busy,
                          onPressed: _submit,
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 24),
                  Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                    TextButton(
                        onPressed: () => _open('https://www.lulaworks.com/terms/'),
                        child: const Text('Terms',
                            style: TextStyle(fontSize: 12.5, color: kMuted))),
                    const Text('·', style: TextStyle(color: kMuted)),
                    TextButton(
                        onPressed: () => _open('https://www.lulaworks.com/privacy/'),
                        child: const Text('Privacy',
                            style: TextStyle(fontSize: 12.5, color: kMuted))),
                  ]),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

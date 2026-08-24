import 'package:flutter/material.dart';

import 'api/api_client.dart';
import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final api = await ApiClient.create();
  runApp(LulaworksApp(api: api));
}

class LulaworksApp extends StatefulWidget {
  const LulaworksApp({super.key, required this.api});
  final ApiClient api;

  @override
  State<LulaworksApp> createState() => _LulaworksAppState();
}

class _LulaworksAppState extends State<LulaworksApp> {
  late bool _signedIn = widget.api.isAuthenticated;

  @override
  void initState() {
    super.initState();
    // A dead session (401 + refresh fails) drops the user back to login instead
    // of trapping them on an error screen.
    widget.api.onAuthFailure = () {
      if (!_signedIn) return;
      widget.api.logout();
      if (mounted) setState(() => _signedIn = false);
    };
  }

  void _onSignedIn() => setState(() => _signedIn = true);
  Future<void> _onSignOut() async {
    await widget.api.logout();
    setState(() => _signedIn = false);
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Lulaworks',
      debugShowCheckedModeBanner: false,
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      home: _signedIn
          ? HomeShell(api: widget.api, onSignOut: _onSignOut)
          : LoginScreen(api: widget.api, onSignedIn: _onSignedIn),
    );
  }
}

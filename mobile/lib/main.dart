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
  /// Drives which root screen shows. Authentication is a single source of truth
  /// here — every sign-in / sign-out flows through this one place.
  late bool _signedIn = widget.api.isAuthenticated;

  /// Guards against double sign-out (rapid taps, or a 401 arriving mid-logout).
  bool _signingOut = false;

  /// Lets us reset the navigation stack from anywhere — the fix for the logout
  /// bug: pushed authenticated routes (Profile, task hubs, …) must be removed,
  /// not just left sitting on top of the login screen.
  final _navKey = GlobalKey<NavigatorState>();

  @override
  void initState() {
    super.initState();
    // A dead session (401 with no working refresh) routes back to Login through
    // the exact same robust path as a manual logout — no duplicate logic, no
    // "Something went wrong" trap, no infinite retry.
    widget.api.onAuthFailure = () {
      if (!_signedIn || _signingOut) return;
      _signOut();
    };
  }

  void _onSignedIn() => setState(() => _signedIn = true);

  /// The ONE sign-out path. Always clears local state (offline-safe), always
  /// resets navigation, and can't be re-entered.
  Future<void> _signOut() async {
    if (_signingOut) return;
    _signingOut = true;
    try {
      // Best-effort server revoke + guaranteed local clear (never throws).
      await widget.api.logout();
    } finally {
      if (mounted) {
        // Swap the root to Login, then drop every route pushed above it so the
        // back button can never return to an authenticated screen.
        setState(() => _signedIn = false);
        _navKey.currentState?.popUntil((r) => r.isFirst);
      }
      _signingOut = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Lulaworks',
      debugShowCheckedModeBanner: false,
      navigatorKey: _navKey,
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      home: _signedIn
          ? HomeShell(api: widget.api, onSignOut: _signOut)
          : LoginScreen(api: widget.api, onSignedIn: _onSignedIn),
    );
  }
}

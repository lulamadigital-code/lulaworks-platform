import 'package:flutter/material.dart';

import 'api/api_client.dart';
import 'screens/login_screen.dart';
import 'screens/projects_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final api = await ApiClient.create();
  runApp(LulaWorksApp(api: api));
}

const _brand = Color(0xFF0E6E6E); // Lulama teal

class LulaWorksApp extends StatefulWidget {
  const LulaWorksApp({super.key, required this.api});
  final ApiClient api;

  @override
  State<LulaWorksApp> createState() => _LulaWorksAppState();
}

class _LulaWorksAppState extends State<LulaWorksApp> {
  late bool _signedIn = widget.api.isAuthenticated;

  void _onSignedIn() => setState(() => _signedIn = true);
  Future<void> _onSignOut() async {
    await widget.api.logout();
    setState(() => _signedIn = false);
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'LulaWorks',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorSchemeSeed: _brand,
        brightness: Brightness.light,
      ),
      darkTheme: ThemeData(
        useMaterial3: true,
        colorSchemeSeed: _brand,
        brightness: Brightness.dark,
      ),
      home: _signedIn
          ? ProjectsScreen(api: widget.api, onSignOut: _onSignOut)
          : LoginScreen(api: widget.api, onSignedIn: _onSignedIn),
    );
  }
}

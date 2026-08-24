import 'package:flutter/material.dart';

/// The Lulaworks wordmark — the real logo shipped with the web app. Picks the
/// white variant on dark backgrounds so it stays legible in either theme.
class BrandLogo extends StatelessWidget {
  const BrandLogo({super.key, this.height = 28});
  final double height;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Image.asset(
      dark ? 'assets/images/logo-white.png' : 'assets/images/logo.png',
      height: height,
      fit: BoxFit.contain,
    );
  }
}

import 'package:flutter/material.dart';

/// Brand tokens mirrored from the website (web/base.html) so the app reads as the
/// same product: the Lulaworks cyan, ink text, and the soft off-white canvas.
const kBrand = Color(0xFF17A2B8); // --primary
const kBrandDark = Color(0xFF0E7C8C); // --primary-d
const kBrandTint = Color(0xFFE6F6F9); // --primary-t
const kInk = Color(0xFF292F4C); // --ink
const kMuted = Color(0xFF676879); // --muted
const kLine = Color(0xFFE6E9EF); // --line
const kBg = Color(0xFFF6F7FB); // --bg

// Status accents from the site's palette.
const kGreen = Color(0xFF00C875);
const kOrange = Color(0xFFFDAB3D);
const kRed = Color(0xFFE2445C);
const kInfo = Color(0xFF3B6FD4);
const kBorderDot = Color(0xFFC4C7D0); // neutral dot for read/settled states

ThemeData buildTheme(Brightness brightness) {
  final light = brightness == Brightness.light;
  final base = ColorScheme.fromSeed(seedColor: kBrand, brightness: brightness);
  // Pin primary to the exact brand cyan so buttons/links match the site.
  final scheme = base.copyWith(primary: light ? kBrand : base.primary);

  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: light ? kBg : null,
    appBarTheme: AppBarTheme(
      backgroundColor: light ? Colors.white : null,
      foregroundColor: light ? kInk : null,
      elevation: 0,
      scrolledUnderElevation: 1,
      centerTitle: false,
    ),
    cardTheme: CardTheme(
      elevation: 0,
      margin: EdgeInsets.zero,
      color: light ? Colors.white : null,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: light ? kLine : Colors.transparent),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: light ? Colors.white : null,
      indicatorColor: kBrandTint,
      elevation: 2,
      labelTextStyle: MaterialStateProperty.all(
        const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    ),
  );
}

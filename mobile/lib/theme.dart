import 'package:flutter/material.dart';

/// Lulaworks colour tokens. Light values mirror the website (web/base.html);
/// dark values are tuned (not inverted) for a legible night theme.
///
/// The tokens are GETTERS that read [lwDark], a brightness flag set once per
/// build at the app root (main.dart) before the tree builds. This lets every
/// existing call site keep using `kInk`, `kMuted`, … unchanged while the colours
/// flip with the theme. (They are therefore non-const — a colour that must adapt
/// can't be a compile-time constant.)
bool lwDark = false;

Color _c(int light, int dark) => Color(lwDark ? dark : light);

// Brand
Color get kBrand => _c(0xFF17A2B8, 0xFF2CB8CE);
Color get kBrandDark => _c(0xFF0E7C8C, 0xFF7FDCEA);
Color get kBrandTint => _c(0xFFE6F6F9, 0xFF10303A);

// Text / structure
Color get kInk => _c(0xFF292F4C, 0xFFE8EAF1);
Color get kMuted => _c(0xFF676879, 0xFF9DA3B7);
Color get kLine => _c(0xFFE6E9EF, 0xFF2C3040);
Color get kBg => _c(0xFFF6F7FB, 0xFF13151D);
/// Card / sheet / app-bar surface — replaces hardcoded `Colors.white` so cards
/// darken in dark mode.
Color get kSurface => _c(0xFFFFFFFF, 0xFF1B1E27);

// Status accents (slightly lifted in dark for contrast)
Color get kGreen => _c(0xFF00C875, 0xFF2ED58D);
Color get kOrange => _c(0xFFFDAB3D, 0xFFFFBB5C);
Color get kRed => _c(0xFFE2445C, 0xFFF26B80);
Color get kInfo => _c(0xFF3B6FD4, 0xFF6E9BF0);
Color get kBorderDot => _c(0xFFC4C7D0, 0xFF3A3F52);

ThemeData buildTheme(Brightness brightness) {
  final light = brightness == Brightness.light;
  // Resolve tokens for THIS theme while building it.
  final prev = lwDark;
  lwDark = !light;
  final scheme = ColorScheme.fromSeed(seedColor: kBrand, brightness: brightness)
      .copyWith(primary: kBrand, surface: kSurface);
  final theme = ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: kBg,
    appBarTheme: AppBarTheme(
      backgroundColor: kSurface,
      foregroundColor: kInk,
      elevation: 0,
      scrolledUnderElevation: 1,
      centerTitle: false,
    ),
    cardTheme: CardTheme(
      elevation: 0,
      margin: EdgeInsets.zero,
      color: kSurface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: kLine),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: kSurface,
      indicatorColor: kBrandTint,
      elevation: 2,
      labelTextStyle: MaterialStateProperty.all(
        const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    ),
  );
  lwDark = prev;
  return theme;
}

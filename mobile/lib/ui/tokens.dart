import 'package:flutter/material.dart';

import '../theme.dart';

/// Lulaworks design-system tokens — the single source for spacing, radius and
/// type. Colours continue to live in theme.dart (kBrand/kInk/…); the semantic
/// aliases in [LwColor] map onto them so screens can read intent, not hues.
///
/// Use these instead of arbitrary numbers:
///   padding: const EdgeInsets.all(LwSpace.md)   // not EdgeInsets.all(13)
///   borderRadius: LwRadius.card                 // not Radius.circular(11)
///   style: LwType.title                         // not TextStyle(fontSize: 17…)

/// 4-based spacing scale. Names map to the step, not the pixel count, so layouts
/// stay consistent and re-tunable.
abstract final class LwSpace {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 20;
  static const double xxl = 24;
  static const double xxxl = 32;
  static const double huge = 48;

  // Common ready-made insets (avoid re-allocating everywhere).
  static const EdgeInsets page = EdgeInsets.all(lg);
  static const EdgeInsets card = EdgeInsets.all(lg);
  static const EdgeInsets listRow = EdgeInsets.symmetric(horizontal: lg, vertical: md);
}

/// One small set of corner radii — cohesive, not per-component invention.
abstract final class LwRadius {
  static const Radius smR = Radius.circular(8);
  static const Radius mdR = Radius.circular(12);
  static const Radius lgR = Radius.circular(16);
  static const BorderRadius sm = BorderRadius.all(smR);
  static const BorderRadius md = BorderRadius.all(mdR);
  static const BorderRadius lg = BorderRadius.all(lgR);
  static const BorderRadius card = md;
  static const BorderRadius pill = BorderRadius.all(Radius.circular(999));
}

/// The Lulaworks type scale. Weight + size + spacing communicate hierarchy:
/// page title → section → body → metadata. Colour defaults to kInk; pass a
/// colour for muted/secondary text or use [LwType.muted].
abstract final class LwType {
  static const TextStyle display = TextStyle(
      fontSize: 26, fontWeight: FontWeight.w700, color: kInk, letterSpacing: -0.5, height: 1.15);
  static const TextStyle headline = TextStyle(
      fontSize: 22, fontWeight: FontWeight.w700, color: kInk, letterSpacing: -0.4, height: 1.2);
  static const TextStyle title = TextStyle(
      fontSize: 17, fontWeight: FontWeight.w700, color: kInk, letterSpacing: -0.2);
  static const TextStyle section = TextStyle(
      fontSize: 11, fontWeight: FontWeight.w700, color: kMuted, letterSpacing: 0.6);
  static const TextStyle bodyLg = TextStyle(fontSize: 15.5, color: kInk, height: 1.35);
  static const TextStyle body = TextStyle(fontSize: 14, color: kInk, height: 1.35);
  static const TextStyle bodySm = TextStyle(fontSize: 12.5, color: kInk, height: 1.3);
  static const TextStyle label = TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, color: kInk);
  static const TextStyle caption = TextStyle(fontSize: 11.5, color: kMuted);

  static const TextStyle muted = TextStyle(fontSize: 14, color: kMuted, height: 1.35);
}

/// Semantic colour aliases over the theme tokens — read intent at the call site.
abstract final class LwColor {
  static const Color primary = kBrand;
  static const Color primaryDark = kBrandDark;
  static const Color primaryTint = kBrandTint;
  static const Color ink = kInk;
  static const Color muted = kMuted;
  static const Color border = kLine;
  static const Color surface = Colors.white;
  static const Color background = kBg;
  static const Color success = kGreen;
  static const Color warning = kOrange;
  static const Color error = kRed;
  static const Color info = kInfo;
}

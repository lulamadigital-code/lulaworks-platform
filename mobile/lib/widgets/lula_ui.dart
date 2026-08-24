import 'package:flutter/material.dart';

import '../theme.dart';

/// Shared Lulaworks form + button primitives so every screen looks the same.
/// Labels stay visible above the field (never placeholder-only), focus is the
/// brand teal, errors turn the border red with a message below.

class LulaTextField extends StatelessWidget {
  const LulaTextField({
    super.key,
    required this.controller,
    required this.label,
    this.hint,
    this.keyboardType,
    this.obscureText = false,
    this.required = false,
    this.enabled = true,
    this.maxLines = 1,
    this.errorText,
    this.suffix,
    this.autofillHints,
    this.textInputAction,
    this.onChanged,
    this.onSubmitted,
  });

  final TextEditingController controller;
  final String label;
  final String? hint;
  final TextInputType? keyboardType;
  final bool obscureText;
  final bool required;
  final bool enabled;
  final int maxLines;
  final String? errorText;
  final Widget? suffix;
  final Iterable<String>? autofillHints;
  final TextInputAction? textInputAction;
  final ValueChanged<String>? onChanged;
  final ValueChanged<String>? onSubmitted;

  @override
  Widget build(BuildContext context) {
    OutlineInputBorder border(Color c) => OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: c, width: 1));
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      RichText(
        text: TextSpan(
          text: label,
          style: const TextStyle(
              fontSize: 13, fontWeight: FontWeight.w600, color: kInk),
          children: required
              ? const [TextSpan(text: ' *', style: TextStyle(color: kRed))]
              : null,
        ),
      ),
      const SizedBox(height: 6),
      TextField(
        controller: controller,
        keyboardType: keyboardType,
        obscureText: obscureText,
        enabled: enabled,
        maxLines: obscureText ? 1 : maxLines,
        autofillHints: autofillHints,
        textInputAction: textInputAction,
        onChanged: onChanged,
        onSubmitted: onSubmitted,
        style: const TextStyle(fontSize: 15, color: kInk),
        decoration: InputDecoration(
          hintText: hint,
          hintStyle: const TextStyle(color: kMuted, fontSize: 14.5),
          errorText: errorText,
          suffixIcon: suffix,
          isDense: true,
          filled: true,
          fillColor: enabled ? Colors.white : kBg,
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
          border: border(kLine),
          enabledBorder: border(kLine),
          focusedBorder: border(kBrand),
          errorBorder: border(kRed),
          focusedErrorBorder: border(kRed),
        ),
      ),
    ]);
  }
}

/// Dropdown matching the LulaTextField look (labelled, white, brand focus).
class LulaDropdown<T> extends StatelessWidget {
  const LulaDropdown({
    super.key,
    required this.label,
    required this.value,
    required this.items,
    required this.onChanged,
    this.required = false,
  });

  final String label;
  final T? value;
  final List<DropdownMenuItem<T>> items;
  final ValueChanged<T?> onChanged;
  final bool required;

  @override
  Widget build(BuildContext context) {
    OutlineInputBorder border(Color c) => OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: c, width: 1));
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      RichText(
        text: TextSpan(
          text: label,
          style: const TextStyle(
              fontSize: 13, fontWeight: FontWeight.w600, color: kInk),
          children: required
              ? const [TextSpan(text: ' *', style: TextStyle(color: kRed))]
              : null,
        ),
      ),
      const SizedBox(height: 6),
      DropdownButtonFormField<T>(
        value: value,
        items: items,
        onChanged: onChanged,
        isExpanded: true,
        style: const TextStyle(fontSize: 15, color: kInk),
        icon: const Icon(Icons.keyboard_arrow_down, color: kMuted),
        decoration: InputDecoration(
          isDense: true,
          filled: true,
          fillColor: Colors.white,
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
          border: border(kLine),
          enabledBorder: border(kLine),
          focusedBorder: border(kBrand),
        ),
      ),
    ]);
  }
}

/// Full-width primary button, ~50px, with an in-button loading state that
/// prevents double submission.
class LulaButton extends StatelessWidget {
  const LulaButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.loading = false,
    this.loadingLabel,
    this.icon,
  });

  final String label;
  final VoidCallback? onPressed;
  final bool loading;
  final String? loadingLabel;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 50,
      width: double.infinity,
      child: FilledButton(
        onPressed: loading ? null : onPressed,
        style: FilledButton.styleFrom(
            backgroundColor: kBrand,
            disabledBackgroundColor: kBrand.withOpacity(0.5),
            foregroundColor: Colors.white,
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12))),
        child: loading
            ? Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                        strokeWidth: 2, color: Colors.white)),
                const SizedBox(width: 10),
                Text(loadingLabel ?? '$label…'),
              ])
            : Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                if (icon != null) ...[Icon(icon, size: 19), const SizedBox(width: 8)],
                Text(label,
                    style: const TextStyle(
                        fontSize: 15.5, fontWeight: FontWeight.w600)),
              ]),
      ),
    );
  }
}


/// A hairline progress bar that hugs the bottom edge of a task card. [pct] is
/// 0–100; the brand fill grows left-to-right over a faint track.
class TaskProgressEdge extends StatelessWidget {
  const TaskProgressEdge(this.pct, {super.key, this.height = 3});
  final num pct;
  final double height;

  @override
  Widget build(BuildContext context) {
    final v = (pct.clamp(0, 100)) / 100.0;
    return SizedBox(
      height: height,
      width: double.infinity,
      child: Stack(children: [
        Container(color: kLine),
        FractionallySizedBox(
          widthFactor: v.toDouble(),
          alignment: Alignment.centerLeft,
          child: Container(color: kBrand),
        ),
      ]),
    );
  }
}


/// Human, state-aware due label + colour from an ISO date. Normal stays muted;
/// only "today" (warning) and overdue (error) get colour (§37).
(String, Color, bool) dueInfo(String? iso) {
  if (iso == null || iso.isEmpty) return ('', kMuted, false);
  final d = DateTime.tryParse(iso);
  if (d == null) return ('', kMuted, false);
  final now = DateTime.now();
  final due = DateTime(d.year, d.month, d.day);
  final t0 = DateTime(now.year, now.month, now.day);
  final diff = due.difference(t0).inDays;
  const wd = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug',
    'Sep', 'Oct', 'Nov', 'Dec'];
  if (diff < 0) return ('Overdue · ${-diff}d', kRed, true);
  if (diff == 0) return ('Due today', kOrange, true);
  if (diff == 1) return ('Due tomorrow', kInk, false);
  if (diff <= 6) return ('Due ${wd[due.weekday - 1]}', kMuted, false);
  return ('Due ${due.day} ${mon[due.month - 1]}', kMuted, false);
}

/// A small due-date pill coloured by urgency. Empty when there's no date.
class DueChip extends StatelessWidget {
  const DueChip(this.iso, {super.key});
  final String? iso;
  @override
  Widget build(BuildContext context) {
    final (label, color, warn) = dueInfo(iso);
    if (label.isEmpty) return const SizedBox.shrink();
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(
          color: warn ? color.withOpacity(0.12) : kBg,
          borderRadius: BorderRadius.circular(8)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(warn ? Icons.schedule : Icons.event_outlined, size: 13, color: color),
        const SizedBox(width: 4),
        Text(label,
            style: TextStyle(
                fontSize: 12, fontWeight: FontWeight.w600, color: color)),
      ]),
    );
  }
}

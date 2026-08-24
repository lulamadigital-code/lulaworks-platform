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

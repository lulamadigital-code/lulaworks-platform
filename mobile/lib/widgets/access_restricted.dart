import 'package:flutter/material.dart';

import '../theme.dart';

/// Shown when a user reaches a screen (or section) their permissions don't
/// cover. Hiding navigation is never the security boundary — the backend still
/// refuses the data — but if a gated surface is opened anyway, this is the calm
/// fallback: no crash, no raw API error, just a clear message and a way back.
class AccessRestricted extends StatelessWidget {
  const AccessRestricted({
    super.key,
    this.title = 'Access restricted',
    this.message = "You don't have permission to access this area.",
    this.inScaffold = true,
  });

  final String title;
  final String message;

  /// Wrap in its own Scaffold+AppBar (a pushed route). Set false to embed the
  /// body inside a section that already has its own scaffold.
  final bool inScaffold;

  @override
  Widget build(BuildContext context) {
    final body = Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                  color: kBrandTint, borderRadius: BorderRadius.circular(18)),
              child: const Icon(Icons.lock_outline, color: kBrandDark, size: 30),
            ),
            const SizedBox(height: 18),
            Text(title,
                textAlign: TextAlign.center,
                style: const TextStyle(
                    fontSize: 18, fontWeight: FontWeight.w700, color: kInk)),
            const SizedBox(height: 6),
            Text(message,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 13.5, color: kMuted, height: 1.4)),
            if (inScaffold && Navigator.of(context).canPop()) ...[
              const SizedBox(height: 22),
              OutlinedButton.icon(
                onPressed: () => Navigator.of(context).maybePop(),
                icon: const Icon(Icons.arrow_back, size: 18),
                label: const Text('Back'),
              ),
            ],
          ],
        ),
      ),
    );
    if (!inScaffold) return body;
    return Scaffold(
      appBar: AppBar(scrolledUnderElevation: 1),
      body: body,
    );
  }
}

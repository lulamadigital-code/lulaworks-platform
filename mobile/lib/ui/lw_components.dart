import 'package:flutter/material.dart';

import '../theme.dart';
import 'tokens.dart';

/// Lulaworks shared UI components — the patterns every screen should reuse
/// instead of re-inventing empty/error states and action clutter. Additive and
/// non-breaking: screens adopt these incrementally.

/// A guiding empty state — never a bare "No data". Explains what the thing is
/// and offers the next step. (§26)
class LwEmptyState extends StatelessWidget {
  const LwEmptyState({
    super.key,
    required this.icon,
    required this.title,
    this.message,
    this.actionLabel,
    this.onAction,
  });
  final IconData icon;
  final String title;
  final String? message;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(LwSpace.xxxl),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 64,
            height: 64,
            decoration: const BoxDecoration(color: kBrandTint, shape: BoxShape.circle),
            child: Icon(icon, color: kBrandDark, size: 30),
          ),
          const SizedBox(height: LwSpace.lg),
          Text(title, style: LwType.title, textAlign: TextAlign.center),
          if (message != null) ...[
            const SizedBox(height: LwSpace.sm),
            Text(message!, style: LwType.muted, textAlign: TextAlign.center),
          ],
          if (actionLabel != null && onAction != null) ...[
            const SizedBox(height: LwSpace.xl),
            FilledButton.icon(
              onPressed: onAction,
              icon: const Icon(Icons.add, size: 18),
              label: Text(actionLabel!),
            ),
          ],
        ]),
      ),
    );
  }
}

/// A recoverable error state — says what happened and offers a retry. (§29)
class LwErrorState extends StatelessWidget {
  const LwErrorState({super.key, this.message, this.onRetry, this.offline = false});
  final String? message;
  final VoidCallback? onRetry;
  final bool offline;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(LwSpace.xxxl),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(offline ? Icons.cloud_off : Icons.error_outline,
              size: 44, color: offline ? kOrange : kMuted),
          const SizedBox(height: LwSpace.md),
          Text(
            message ??
                (offline
                    ? "You're offline. Showing what we last synced."
                    : "Something went wrong."),
            style: LwType.muted,
            textAlign: TextAlign.center,
          ),
          if (onRetry != null) ...[
            const SizedBox(height: LwSpace.lg),
            OutlinedButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh, size: 18),
                label: const Text('Retry')),
          ],
        ]),
      ),
    );
  }
}

/// One secondary action in an [LwActionMenu].
class LwAction {
  const LwAction(this.label, this.icon, this.onSelected, {this.destructive = false});
  final String label;
  final IconData icon;
  final VoidCallback onSelected;
  final bool destructive;
}

/// An overflow "…" menu for secondary/contextual actions, so the screen keeps a
/// single clear primary action instead of a row of equal buttons. (§8/§10)
class LwActionMenu extends StatelessWidget {
  const LwActionMenu({super.key, required this.actions, this.tooltip = 'More'});
  final List<LwAction> actions;
  final String tooltip;

  @override
  Widget build(BuildContext context) {
    final items = actions.where((a) => true).toList();
    return PopupMenuButton<int>(
      tooltip: tooltip,
      icon: const Icon(Icons.more_vert),
      onSelected: (i) => items[i].onSelected(),
      itemBuilder: (context) => [
        for (var i = 0; i < items.length; i++)
          PopupMenuItem<int>(
            value: i,
            child: Row(children: [
              Icon(items[i].icon, size: 18,
                  color: items[i].destructive ? kRed : kMuted),
              const SizedBox(width: LwSpace.md),
              Text(items[i].label,
                  style: TextStyle(color: items[i].destructive ? kRed : kInk)),
            ]),
          ),
      ],
    );
  }
}

/// A section header with an optional trailing action (e.g. "View all"). (§11)
class LwSection extends StatelessWidget {
  const LwSection(this.title, {super.key, this.actionLabel, this.onAction, this.trailing});
  final String title;
  final String? actionLabel;
  final VoidCallback? onAction;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: LwSpace.sm, bottom: LwSpace.sm),
      child: Row(children: [
        Text(title.toUpperCase(), style: LwType.section),
        const Spacer(),
        if (trailing != null) trailing!,
        if (actionLabel != null && onAction != null)
          TextButton(onPressed: onAction, child: Text(actionLabel!)),
      ]),
    );
  }
}

/// Confirmation for dangerous / irreversible actions — and ONLY those. (§30)
/// Returns true if the user confirmed.
Future<bool> lwConfirm(
  BuildContext context, {
  required String title,
  required String message,
  String confirmLabel = 'Confirm',
  String cancelLabel = 'Cancel',
  bool destructive = false,
}) async {
  final ok = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text(title),
      content: Text(message),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(cancelLabel)),
        FilledButton(
          style: destructive
              ? FilledButton.styleFrom(backgroundColor: kRed)
              : null,
          onPressed: () => Navigator.pop(ctx, true),
          child: Text(confirmLabel),
        ),
      ],
    ),
  );
  return ok ?? false;
}

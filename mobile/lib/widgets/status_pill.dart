import 'package:flutter/material.dart';

import '../theme.dart';

/// A compact status chip that colours itself from the status keyword — green for
/// settled/positive, amber for in-flight, red for negative, neutral for draft.
/// Colour communicates state (status only), never decoration.
class StatusPill extends StatelessWidget {
  const StatusPill({super.key, required this.status});
  final String status;

  static const _good = {
    'approved', 'complete', 'completed', 'received', 'active', 'paid',
    'awarded', 'fulfilled', 'accepted'
  };
  static const _bad = {
    'rejected', 'cancelled', 'blacklisted', 'overdue', 'lost', 'expired'
  };
  static const _busy = {
    'in_review', 'submitted', 'pending', 'sent', 'awaiting', 'on_hold',
    'draft_po', 'partial'
  };

  @override
  Widget build(BuildContext context) {
    final s = status.toLowerCase();
    final Color c = _good.contains(s)
        ? kGreen
        : _bad.contains(s)
            ? kRed
            : _busy.contains(s)
                ? kOrange
                : Theme.of(context).colorScheme.outline;
    final label = (status.isEmpty ? '—' : status.replaceAll('_', ' '));
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
          color: c.withOpacity(0.14), borderRadius: BorderRadius.circular(12)),
      child: Text(
        label.isEmpty ? '—' : label[0].toUpperCase() + label.substring(1),
        style: TextStyle(color: c, fontSize: 12, fontWeight: FontWeight.w600),
      ),
    );
  }
}

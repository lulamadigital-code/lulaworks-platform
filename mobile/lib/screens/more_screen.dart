import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../nav/app_nav.dart';
import '../theme.dart';

/// The "More" hub — everything that doesn't live in the primary tabs, grouped
/// (Business · Relationships · Operations · Administration · Account). The whole
/// menu comes from the central [moreGroupsFor] config: entries appear only when
/// permitted, and never duplicate a tab the user already has.
class MoreScreen extends StatelessWidget {
  const MoreScreen({super.key, required this.api, required this.actions});
  final ApiClient api;
  final NavActions actions;

  @override
  Widget build(BuildContext context) {
    final shownTabIds = {for (final t in bottomTabsFor(api)) t.id};
    final groups = moreGroupsFor(api, shownTabIds);
    return Scaffold(
      appBar: AppBar(title: const Text('More'), scrolledUnderElevation: 1),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 28),
        children: [
          for (final g in groups) ...[
            _label(g.label),
            const SizedBox(height: 8),
            _group(context, g.items),
            const SizedBox(height: 20),
          ],
        ],
      ),
    );
  }

  Widget _label(String s) => Padding(
        padding: const EdgeInsets.only(left: 4),
        child: Text(s,
            style: const TextStyle(
                fontSize: 11.5, fontWeight: FontWeight.w700,
                letterSpacing: 0.6, color: kMuted)),
      );

  Widget _group(BuildContext context, List<MoreItem> items) {
    return Container(
      decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: kLine)),
      child: Column(children: [
        for (int i = 0; i < items.length; i++) ...[
          if (i > 0) const Divider(height: 1, indent: 60),
          ListTile(
            contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
            leading: Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                  color: kBrand.withOpacity(0.08),
                  borderRadius: BorderRadius.circular(11)),
              child: Icon(items[i].icon, color: kBrandDark, size: 21),
            ),
            title: Text(items[i].title,
                style: const TextStyle(
                    fontSize: 15, fontWeight: FontWeight.w600, color: kInk)),
            subtitle: Text(items[i].subtitle,
                style: const TextStyle(fontSize: 12.5, color: kMuted)),
            trailing: const Icon(Icons.chevron_right, color: kMuted),
            onTap: () => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => items[i].build(api, actions))),
          ),
        ],
      ]),
    );
  }
}

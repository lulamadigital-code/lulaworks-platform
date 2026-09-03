import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../screens/company_settings_screen.dart';
import '../theme.dart';

/// Progressive company-setup state (mirrors GET /api/v1/company/setup/). The
/// backend is authoritative; this only decides what the app shows.
class SetupStatus {
  SetupStatus({
    required this.overallPercentage,
    required this.requiredComplete,
    required this.itemsRemaining,
    required this.canEdit,
    required this.sections,
  });

  final int overallPercentage;
  final bool requiredComplete;
  final int itemsRemaining;
  final bool canEdit;
  final List<SetupSection> sections;

  static SetupStatus? fromJson(dynamic body) {
    if (body is! Map) return null;
    final secs = <SetupSection>[];
    final raw = body['sections'];
    if (raw is Map) {
      raw.forEach((k, v) {
        if (v is Map) secs.add(SetupSection.fromJson('$k', v.cast<String, dynamic>()));
      });
    }
    return SetupStatus(
      overallPercentage: (body['overall_percentage'] as num?)?.round() ?? 0,
      requiredComplete: body['required_complete'] == true,
      itemsRemaining: (body['items_remaining'] as num?)?.toInt() ?? 0,
      canEdit: body['can_edit'] == true,
      sections: secs,
    );
  }
}

class SetupSection {
  SetupSection(this.key, this.label, this.complete, this.recommended);
  final String key;
  final String label;
  final bool complete;
  final bool recommended;
  factory SetupSection.fromJson(String key, Map<String, dynamic> j) => SetupSection(
        key, '${j['label'] ?? key}', j['complete'] == true, j['recommended'] == true);
}

/// The non-blocking dashboard setup card. Show only while required setup is
/// incomplete and the viewer can fix it.
class SetupCard extends StatelessWidget {
  const SetupCard({super.key, required this.api, required this.status, this.onOpen});
  final ApiClient api;
  final SetupStatus status;
  final VoidCallback? onOpen;

  @override
  Widget build(BuildContext context) {
    final s = status;
    return Container(
      decoration: BoxDecoration(
        color: kBrandTint,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: kBrand.withOpacity(0.35)),
      ),
      padding: const EdgeInsets.all(16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          const Icon(Icons.rocket_launch_outlined, size: 20, color: kBrandDark),
          const SizedBox(width: 8),
          const Expanded(
            child: Text('Finish your company setup',
                style: TextStyle(fontWeight: FontWeight.w700, color: kInk))),
          Text('${s.overallPercentage}%',
              style: const TextStyle(fontWeight: FontWeight.w700, color: kBrandDark)),
        ]),
        const SizedBox(height: 8),
        ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: LinearProgressIndicator(
            value: s.overallPercentage / 100,
            minHeight: 7,
            backgroundColor: kLine,
            valueColor: const AlwaysStoppedAnimation(kBrand),
          ),
        ),
        const SizedBox(height: 8),
        Text(
          '${s.itemsRemaining} item${s.itemsRemaining == 1 ? '' : 's'} left · '
          'complete your details to unlock invoicing & professional documents.',
          style: const TextStyle(fontSize: 12.5, color: kMuted)),
        if (s.sections.isNotEmpty) ...[
          const SizedBox(height: 10),
          Wrap(spacing: 6, runSpacing: 6, children: [
            for (final sec in s.sections)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
                decoration: BoxDecoration(
                  color: sec.complete ? const Color(0xFFE4F1EB) : Colors.white,
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(color: kLine),
                ),
                child: Text('${sec.complete ? '✓' : '○'} ${sec.label}',
                    style: TextStyle(
                        fontSize: 11,
                        color: sec.complete ? const Color(0xFF0A8F57) : kMuted)),
              ),
          ]),
        ],
        const SizedBox(height: 12),
        Align(
          alignment: Alignment.centerLeft,
          child: FilledButton(
            style: FilledButton.styleFrom(backgroundColor: kBrand),
            onPressed: () => _openSettings(context),
            child: const Text('Complete setup'),
          ),
        ),
      ]),
    );
  }

  void _openSettings(BuildContext context) {
    (onOpen ??
        () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => CompanySettingsScreen(api: api))))();
  }
}

/// Show a friendly block dialog when an action is refused because company setup
/// is incomplete. Call from a catch: `if (e.isSetupRequired) showCompanySetupDialog(...)`.
Future<void> showCompanySetupDialog(
    BuildContext context, ApiClient api, ApiException e) {
  final missing = e.missing;
  final canEdit = api.canManageCompany;
  final action = '${e.data?['action'] ?? ''}';
  return showDialog<void>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text(_titleFor(action)),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(e.message.isNotEmpty
              ? e.message
              : 'Complete the required company information first.'),
          if (missing.isNotEmpty) ...[
            const SizedBox(height: 14),
            const Text('Missing:', style: TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            for (final m in missing)
              Padding(
                padding: const EdgeInsets.only(top: 3),
                child: Text('•  ${m['label']}',
                    style: const TextStyle(color: kInk))),
          ],
          if (!canEdit) ...[
            const SizedBox(height: 14),
            const Text(
              'This company setting needs to be completed by a company administrator.',
              style: TextStyle(fontSize: 12.5, color: kMuted)),
          ],
        ],
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cancel')),
        if (canEdit)
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: kBrand),
            onPressed: () {
              Navigator.of(ctx).pop();
              Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => CompanySettingsScreen(api: api)));
            },
            child: const Text('Complete setup'),
          ),
      ],
    ),
  );
}

String _titleFor(String action) {
  if (action.contains('INVOICE')) return 'Invoice unavailable';
  if (action.contains('QUOTATION')) return 'Quotation unavailable';
  if (action.contains('DELIVERY')) return 'Delivery note unavailable';
  return 'Company setup required';
}

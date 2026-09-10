import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../screens/customer_detail_screen.dart';
import '../screens/quotations_screen.dart' show QuotationDetailScreen;
import '../theme.dart';

/// The business-transaction graph for one record — its related records grouped
/// by relationship, fetched from GET /related/<type>/<id>/. Read-only; taps
/// open the app's own screen for the types that have one (customer, quotation).
class RelatedRecords extends StatefulWidget {
  const RelatedRecords({super.key, required this.api, required this.type, required this.id});
  final ApiClient api;
  final String type;
  final String id;

  @override
  State<RelatedRecords> createState() => _RelatedRecordsState();
}

class _RelatedRecordsState extends State<RelatedRecords> {
  late final Future<List<dynamic>> _future = _load();

  Future<List<dynamic>> _load() async {
    final body = await widget.api.get('/related/${widget.type}/${widget.id}/');
    if (body is Map && body['sections'] is List) return body['sections'] as List;
    return const [];
  }

  void _open(Map<String, dynamic> it) {
    final t = '${it['type']}';
    final id = '${it['id']}';
    Widget? screen;
    if (t == 'customer') {
      screen = CustomerDetailScreen(api: widget.api, customerId: id);
    } else if (t == 'quotation') {
      screen = QuotationDetailScreen(api: widget.api, quoteId: id);
    }
    if (screen != null) {
      Navigator.of(context).push(MaterialPageRoute(builder: (_) => screen!));
    }
  }

  bool _navigable(String t) => t == 'customer' || t == 'quotation';

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<List<dynamic>>(
      future: _future,
      builder: (context, snap) {
        final sections = snap.data ?? const [];
        if (sections.isEmpty) return const SizedBox.shrink();
        return Container(
          margin: const EdgeInsets.only(top: 16),
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: kLine),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('RELATED RECORDS',
                style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700,
                    letterSpacing: .5, color: kMuted)),
            const SizedBox(height: 8),
            for (final sec in sections) ...[
              Padding(
                padding: const EdgeInsets.only(top: 6, bottom: 3),
                child: Text('${(sec as Map)['title']}',
                    style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600,
                        color: kMuted)),
              ),
              for (final raw in (sec['items'] as List? ?? const []))
                _row((raw as Map).cast<String, dynamic>()),
            ],
          ]),
        );
      },
    );
  }

  Widget _row(Map<String, dynamic> it) {
    final nav = _navigable('${it['type']}');
    return InkWell(
      onTap: nav ? () => _open(it) : null,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 7, horizontal: 2),
        child: Row(children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${it['label']}',
                  style: const TextStyle(fontWeight: FontWeight.w600, color: kInk, fontSize: 13.5)),
              Text('${it['sub']}',
                  style: const TextStyle(fontSize: 11.5, color: kMuted)),
            ]),
          ),
          if (nav) const Icon(Icons.chevron_right, size: 18, color: kMuted),
        ]),
      ),
    );
  }
}

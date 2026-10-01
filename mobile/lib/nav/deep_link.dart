import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../screens/commercial_documents_screen.dart' show commercialDocumentDetailScreen;
import '../screens/customer_detail_screen.dart';
import '../screens/customer_pos_screen.dart' show CustomerPoDetailScreen;
import '../screens/lead_detail_screen.dart';
import '../screens/project_detail_screen.dart';
import '../screens/quotations_screen.dart' show QuotationDetailScreen;
import '../screens/task_hub_screen.dart';

/// Resolve a backend record URL (the web path a notification carries, e.g.
/// "/quotations/<uuid>/") to the matching in-app screen, and open it. One place
/// so notifications — and later a tapped push — land on the right record.
///
/// Returns true if it navigated. Unknown/unmapped paths return false so the
/// caller can fall back (e.g. just mark the notification read).
Future<bool> openDeepLink(
    BuildContext context, ApiClient api, String? url) async {
  final seg = _parse(url);
  if (seg == null) return false;
  final (type, id) = seg;

  // Project needs its model; fetch it (cached) before opening.
  if (type == 'projects') {
    try {
      final data = (await api.getCached('/projects/$id/')).data;
      final project = Project.fromJson((data as Map).cast<String, dynamic>());
      if (!context.mounted) return false;
      return _push(context, ProjectDetailScreen(api: api, project: project));
    } catch (_) {
      return false;
    }
  }

  final screen = switch (type) {
    'customers' => CustomerDetailScreen(api: api, customerId: id),
    'quotations' => QuotationDetailScreen(api: api, quoteId: id),
    'customer-pos' => CustomerPoDetailScreen(api: api, poId: id),
    'leads' => LeadDetailScreen(api: api, leadId: id),
    'commercial-documents' => commercialDocumentDetailScreen(api: api, docId: id),
    'jobs' => TaskHubScreen(api: api, taskId: id, name: 'Task'),
    _ => null,
  };
  if (screen == null) return false;
  return _push(context, screen);
}

/// True if a backend URL maps to an in-app screen (for showing a chevron, etc.).
bool isDeepLinkable(String? url) => _parse(url) != null;

bool _push(BuildContext context, Widget screen) {
  Navigator.of(context).push(MaterialPageRoute(builder: (_) => screen));
  return true;
}

const _types = {
  'customers', 'quotations', 'customer-pos', 'leads',
  'commercial-documents', 'jobs', 'projects',
};

final _uuid = RegExp(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$');

/// Find a `<type>/<uuid>` pair anywhere in the path — tolerant of a leading
/// host, query string, or trailing slash.
(String, String)? _parse(String? url) {
  if (url == null || url.isEmpty) return null;
  var path = url;
  final q = path.indexOf('?');
  if (q >= 0) path = path.substring(0, q);
  final parts = path.split('/').where((s) => s.isNotEmpty).toList();
  for (var i = 0; i < parts.length - 1; i++) {
    if (_types.contains(parts[i]) && _uuid.hasMatch(parts[i + 1])) {
      return (parts[i], parts[i + 1]);
    }
  }
  return null;
}

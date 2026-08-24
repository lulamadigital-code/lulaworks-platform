import 'package:flutter/material.dart';
import 'package:printing/printing.dart';

import '../api/api_client.dart';

/// Renders an official backend-generated PDF (quotation, tax invoice, delivery
/// note) in-app. Fetches the bytes with auth from the given API [path]; the
/// PdfPreview also offers print/share. The document is the same one the web
/// generates — we never recreate a simplified version (§45).
class PdfViewerScreen extends StatelessWidget {
  const PdfViewerScreen({
    super.key,
    required this.api,
    required this.path,
    required this.title,
  });

  final ApiClient api;
  final String path;
  final String title;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: PdfPreview(
        build: (format) => api.getBytes(path),
        canChangePageFormat: false,
        canChangeOrientation: false,
        canDebug: false,
        loadingWidget: const Center(child: CircularProgressIndicator()),
      ),
    );
  }
}

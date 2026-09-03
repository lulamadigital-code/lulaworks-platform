import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:printing/printing.dart';

import '../api/api_client.dart';
import '../widgets/company_setup.dart';

/// Renders an official backend-generated PDF (quotation, tax invoice, delivery
/// note) in-app. Fetches the bytes with auth from the given API [path]; the
/// PdfPreview also offers print/share. The document is the same one the web
/// generates — we never recreate a simplified version (§45).
///
/// If the backend refuses because company setup is incomplete (HTTP 422), we
/// show the progressive-setup block dialog and pop back — never a raw error.
class PdfViewerScreen extends StatefulWidget {
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
  State<PdfViewerScreen> createState() => _PdfViewerScreenState();
}

class _PdfViewerScreenState extends State<PdfViewerScreen> {
  late final Future<Uint8List> _future = widget.api.getBytes(widget.path);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: FutureBuilder<Uint8List>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            final e = snap.error;
            if (e is ApiException && e.isSetupRequired) {
              WidgetsBinding.instance.addPostFrameCallback((_) async {
                if (!context.mounted) return;
                await showCompanySetupDialog(context, widget.api, e);
                if (context.mounted) Navigator.of(context).maybePop();
              });
              return const SizedBox.shrink();
            }
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(e is ApiException ? e.message
                    : 'Could not load the document.'),
              ),
            );
          }
          final bytes = snap.data!;
          return PdfPreview(
            build: (_) => bytes,
            canChangePageFormat: false,
            canChangeOrientation: false,
            canDebug: false,
            loadingWidget: const Center(child: CircularProgressIndicator()),
          );
        },
      ),
    );
  }
}

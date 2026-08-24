import 'package:flutter/material.dart';

import '../api/api_client.dart';
import 'commercial_documents_screen.dart';
import 'customers_screen.dart';
import 'estimates_screen.dart';
import 'lulama_screen.dart';
import 'my_tasks_screen.dart';
import 'notifications_screen.dart';
import 'purchase_orders_screen.dart';
import 'quotations_screen.dart';
import 'rfq_screen.dart';
import 'suppliers_screen.dart';

/// The "More" hub — a home for modules that don't earn a permanent bottom-nav
/// slot. Entries are permission-gated, and the list grows as modules land
/// (suppliers, RFQs, estimates, POs…). Keeps the bottom bar to five.
class MoreScreen extends StatelessWidget {
  const MoreScreen({super.key, required this.api});
  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    final entries = <_Entry>[
      _Entry('My tasks', 'Work assigned to you', Icons.checklist_rtl,
          () => MyTasksScreen(api: api)),
      _Entry('Notifications', 'Alerts & mentions', Icons.notifications_outlined,
          () => NotificationsScreen(api: api)),
      if (api.canSeeCustomers)
        _Entry('Customers', 'Client database & contacts', Icons.contacts_outlined,
            () => CustomersScreen(api: api)),
      if (api.canSeeQuotes)
        _Entry('Quotations', 'Quotes & workflow', Icons.article_outlined,
            () => QuotationsScreen(api: api)),
      if (api.canSeeCommercial)
        _Entry('Invoices & delivery', 'Tax invoices & delivery notes',
            Icons.receipt_outlined, () => CommercialDocumentsScreen(api: api)),
      if (api.canProcurement)
        _Entry('Suppliers', 'Vendor database', Icons.local_shipping_outlined,
            () => SuppliersScreen(api: api)),
      if (api.canProcurement)
        _Entry('Purchase orders', 'Orders to suppliers', Icons.receipt_long_outlined,
            () => PurchaseOrdersScreen(api: api)),
      if (api.canSeeRfq)
        _Entry('RFQs', 'Requests for quotation', Icons.request_quote_outlined,
            () => RfqScreen(api: api)),
      if (api.canSeeEstimates)
        _Entry('Estimates', 'Pre-quote costing', Icons.calculate_outlined,
            () => EstimatesScreen(api: api)),
      if (api.canGenerateAi)
        _Entry('Lulaworks AI', 'Ask the assistant', Icons.auto_awesome_outlined,
            () => LulamaScreen(api: api)),
    ];
    return Scaffold(
      appBar: AppBar(title: const Text('More')),
      body: entries.isEmpty
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(32),
                child: Text('Nothing here yet.',
                    style: TextStyle(color: Theme.of(context).colorScheme.outline)),
              ),
            )
          : ListView.separated(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: entries.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final e = entries[i];
                return ListTile(
                  leading: Icon(e.icon, color: Theme.of(context).colorScheme.primary),
                  title: Text(e.title),
                  subtitle: Text(e.subtitle),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.of(context)
                      .push(MaterialPageRoute(builder: (_) => e.build())),
                );
              },
            ),
    );
  }
}

class _Entry {
  _Entry(this.title, this.subtitle, this.icon, this.build);
  final String title;
  final String subtitle;
  final IconData icon;
  final Widget Function() build;
}

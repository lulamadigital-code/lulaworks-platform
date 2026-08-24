import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import 'estimates_screen.dart';
import 'purchase_orders_screen.dart';
import 'rfq_screen.dart';
import 'suppliers_screen.dart';

/// The Purchasing hub — procurement modules in one place, each permission-gated.
class PurchasingScreen extends StatelessWidget {
  const PurchasingScreen({super.key, required this.api});
  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    final items = <_Item>[
      if (api.canProcurement)
        _Item('Suppliers', 'Vendor database', Icons.local_shipping_outlined,
            () => SuppliersScreen(api: api)),
      if (api.canProcurement)
        _Item('Purchase orders', 'Orders to suppliers', Icons.receipt_long_outlined,
            () => PurchaseOrdersScreen(api: api)),
      if (api.canSeeRfq)
        _Item('RFQs', 'Requests for quotation', Icons.request_quote_outlined,
            () => RfqScreen(api: api)),
      if (api.canSeeEstimates)
        _Item('Estimates', 'Pre-quote costing', Icons.calculate_outlined,
            () => EstimatesScreen(api: api)),
    ];
    return Scaffold(
      appBar: AppBar(title: const Text('Purchasing'), scrolledUnderElevation: 1),
      body: items.isEmpty
          ? _empty(context)
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 28),
              children: [
                Container(
                  decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(14),
                      border: Border.all(color: kLine)),
                  child: Column(children: [
                    for (int i = 0; i < items.length; i++) ...[
                      if (i > 0) const Divider(height: 1, indent: 60),
                      _tile(context, items[i]),
                    ],
                  ]),
                ),
              ],
            ),
    );
  }

  Widget _tile(BuildContext context, _Item e) => ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
        leading: Container(
          width: 40,
          height: 40,
          decoration: BoxDecoration(
              color: kBrand.withOpacity(0.08),
              borderRadius: BorderRadius.circular(11)),
          child: Icon(e.icon, color: kBrandDark, size: 21),
        ),
        title: Text(e.title,
            style: const TextStyle(
                fontSize: 15, fontWeight: FontWeight.w600, color: kInk)),
        subtitle: Text(e.subtitle,
            style: const TextStyle(fontSize: 12.5, color: kMuted)),
        trailing: const Icon(Icons.chevron_right, color: kMuted),
        onTap: () =>
            Navigator.of(context).push(MaterialPageRoute(builder: (_) => e.build())),
      );

  Widget _empty(BuildContext context) => const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.shopping_cart_outlined, size: 46, color: kMuted),
            SizedBox(height: 12),
            Text('Nothing here for your role',
                style: TextStyle(
                    fontSize: 15, fontWeight: FontWeight.w600, color: kInk)),
            SizedBox(height: 2),
            Text('Procurement tools appear here when you have access.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 13, color: kMuted)),
          ]),
        ),
      );
}

class _Item {
  _Item(this.title, this.subtitle, this.icon, this.build);
  final String title;
  final String subtitle;
  final IconData icon;
  final Widget Function() build;
}

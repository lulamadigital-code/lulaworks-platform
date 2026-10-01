import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../ui/tokens.dart';
import '../screens/customer_form_screen.dart';
import '../screens/customer_po_form_screen.dart';
import '../screens/invoice_form_screen.dart';
import '../screens/purchase_order_form_screen.dart';
import '../screens/quotation_form_screen.dart';
import '../screens/supplier_form_screen.dart';

/// One fast way to create anything (§21). Opens a permission-aware sheet of the
/// records a user can start standalone, each routed to its real form screen.
/// Permissions gate what's shown; the backend still enforces on submit.
void showGlobalCreate(BuildContext context, ApiClient api) {
  final items = <_CreateItem>[
    if (api.canCreateQuote)
      _CreateItem('Quotation', Icons.article_outlined,
          (c) => QuotationFormScreen(api: api)),
    if (api.canCreateQuote)
      _CreateItem('Invoice', Icons.receipt_long_outlined,
          (c) => InvoiceFormScreen(api: api)),
    if (api.canManageCustomers)
      _CreateItem('Customer', Icons.person_add_alt,
          (c) => CustomerFormScreen(api: api)),
    if (api.canCreateQuote)
      _CreateItem('Customer PO', Icons.assignment_turned_in_outlined,
          (c) => CustomerPoFormScreen(api: api)),
    if (api.canProcurement)
      _CreateItem('Supplier', Icons.local_shipping_outlined,
          (c) => SupplierFormScreen(api: api)),
    if (api.canProcurement)
      _CreateItem('Purchase order', Icons.shopping_cart_outlined,
          (c) => PurchaseOrderFormScreen(api: api)),
  ];
  if (items.isEmpty) return; // nothing this user may create

  showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    builder: (ctx) => SafeArea(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        const Padding(
          padding: EdgeInsets.fromLTRB(LwSpace.xl, LwSpace.xs, LwSpace.xl, LwSpace.sm),
          child: Align(alignment: Alignment.centerLeft,
              child: Text('Create', style: LwType.title)),
        ),
        for (final it in items)
          ListTile(
            leading: Container(
              width: 38, height: 38,
              decoration: const BoxDecoration(color: kBrandTint, shape: BoxShape.circle),
              child: Icon(it.icon, size: 20, color: kBrandDark),
            ),
            title: Text(it.label, style: LwType.label),
            onTap: () {
              Navigator.pop(ctx);
              Navigator.of(context).push(MaterialPageRoute(builder: it.build));
            },
          ),
        const SizedBox(height: LwSpace.sm),
      ]),
    ),
  );
}

class _CreateItem {
  const _CreateItem(this.label, this.icon, this.build);
  final String label;
  final IconData icon;
  final WidgetBuilder build;
}

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';

/// People we work with — the real humans at your clients. One searchable list
/// across every customer's contacts, with tap-to-call / email / WhatsApp.
/// Parity with the web CRM hub's "People we work with".
class ContactsScreen extends StatefulWidget {
  const ContactsScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<ContactsScreen> createState() => _ContactsScreenState();
}

class _ContactsScreenState extends State<ContactsScreen> {
  final _searchCtl = TextEditingController();
  Timer? _debounce;
  String _query = '';
  late Future<List<Map<String, dynamic>>> _future = _load();

  Future<List<Map<String, dynamic>>> _load() async {
    final q = _query.trim().isEmpty ? '' : '?search=${Uri.encodeQueryComponent(_query.trim())}';
    final body = await widget.api.get('/customer-contacts/$q');
    return pageResults(body).cast<Map<String, dynamic>>();
  }

  void _reload() => setState(() => _future = _load());

  void _onSearchChanged(String v) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), () {
      if (!mounted) return;
      setState(() {
        _query = v;
        _future = _load();
      });
    });
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _searchCtl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('People we work with'),
        scrolledUnderElevation: 1,
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(58),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
            child: TextField(
              controller: _searchCtl,
              onChanged: _onSearchChanged,
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                hintText: 'Search name, role, email or number',
                prefixIcon: const Icon(Icons.search, size: 20),
                suffixIcon: _query.isEmpty
                    ? null
                    : IconButton(
                        icon: const Icon(Icons.clear, size: 18),
                        onPressed: () {
                          _searchCtl.clear();
                          _onSearchChanged('');
                        },
                      ),
                isDense: true,
                filled: true,
                fillColor: Colors.white,
                contentPadding: const EdgeInsets.symmetric(vertical: 8),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: const BorderSide(color: kLine),
                ),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: const BorderSide(color: kLine),
                ),
              ),
            ),
          ),
        ),
      ),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => _reload(),
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator(color: kBrand));
            }
            if (snap.hasError) {
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('Could not load contacts.', style: TextStyle(color: kMuted))),
              ]);
            }
            final people = snap.data!;
            if (people.isEmpty) {
              return ListView(children: [
                const SizedBox(height: 120),
                Center(
                    child: Text(
                        _query.isEmpty
                            ? 'No contacts yet. Add the people you deal\nwith on each customer record.'
                            : 'No one matches "$_query".',
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: kMuted))),
              ]);
            }
            return ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: people.length,
              separatorBuilder: (_, __) => const SizedBox(height: 10),
              itemBuilder: (_, i) => _card(people[i]),
            );
          },
        ),
      ),
    );
  }

  Widget _card(Map<String, dynamic> p) {
    final name = '${p['full_name'] ?? ''}'.trim();
    final role = '${p['job_title'] ?? ''}'.trim();
    final company = '${p['customer_name'] ?? ''}'.trim();
    final email = '${p['email'] ?? ''}'.trim();
    final mobile = '${p['mobile'] ?? p['telephone'] ?? ''}'.trim();
    final whatsapp = '${p['whatsapp'] ?? ''}'.trim();
    final isPrimary = p['is_primary'] == true;

    final sub = [role.isEmpty ? 'Contact' : role, if (company.isNotEmpty) company].join(' · ');

    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: kLine),
      ),
      child: Row(children: [
        Container(
          width: 44,
          height: 44,
          alignment: Alignment.center,
          decoration: BoxDecoration(
              color: kBrand.withOpacity(0.10), borderRadius: BorderRadius.circular(12)),
          child: Text(name.isEmpty ? '?' : name.substring(0, 1).toUpperCase(),
              style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700, color: kBrandDark)),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Flexible(
                child: Text(name.isEmpty ? 'Unnamed' : name,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontWeight: FontWeight.w600, color: kInk, fontSize: 15)),
              ),
              if (isPrimary) ...[
                const SizedBox(width: 6),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                  decoration:
                      BoxDecoration(color: kBrandTint, borderRadius: BorderRadius.circular(6)),
                  child: const Text('Primary',
                      style: TextStyle(fontSize: 9.5, color: kBrandDark, fontWeight: FontWeight.w700)),
                ),
              ],
            ]),
            const SizedBox(height: 2),
            Text(sub, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 12.5, color: kMuted)),
          ]),
        ),
        if (email.isNotEmpty)
          _reachBtn(Icons.email_outlined, 'Email', () => _launch('mailto:$email')),
        if (mobile.isNotEmpty)
          _reachBtn(Icons.call_outlined, 'Call', () => _launch('tel:$mobile')),
        if (whatsapp.isNotEmpty)
          _reachBtn(Icons.chat_outlined, 'WhatsApp',
              () => _launch('https://wa.me/${whatsapp.replaceAll(RegExp(r'[^0-9]'), '')}')),
      ]),
    );
  }

  Widget _reachBtn(IconData icon, String tip, VoidCallback onTap) {
    return IconButton(
      tooltip: tip,
      visualDensity: VisualDensity.compact,
      icon: Icon(icon, size: 20, color: kBrandDark),
      onPressed: onTap,
    );
  }

  Future<void> _launch(String uri) async {
    final messenger = ScaffoldMessenger.of(context);
    final u = Uri.parse(uri);
    if (!await launchUrl(u, mode: LaunchMode.externalApplication)) {
      messenger.showSnackBar(const SnackBar(content: Text('Could not open that app.')));
    }
  }
}

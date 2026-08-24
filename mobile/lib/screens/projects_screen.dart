import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../theme.dart';
import 'project_detail_screen.dart';

/// Projects (jobs) — searchable list of clean job cards, active first. Matches
/// the Home/Profile design language: neutral cards, hairline borders, status
/// pills, skeleton loading.
class ProjectsScreen extends StatefulWidget {
  const ProjectsScreen({super.key, required this.api, required this.onSignOut});
  final ApiClient api;
  final Future<void> Function() onSignOut;

  @override
  State<ProjectsScreen> createState() => _ProjectsScreenState();
}

class _ProjectsScreenState extends State<ProjectsScreen> {
  late Future<List<Project>> _future = _load('');
  final _search = TextEditingController();
  Timer? _debounce;

  Future<List<Project>> _load(String q) async {
    final path = q.trim().isEmpty
        ? '/projects/'
        : '/projects/?search=${Uri.encodeQueryComponent(q.trim())}';
    final list = pageResults(await widget.api.get(path)).map(Project.fromJson).toList();
    // Active jobs first, then the rest.
    list.sort((a, b) => (a.isReady ? 0 : 1) - (b.isReady ? 0 : 1));
    return list;
  }

  void _onSearch(String q) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350),
        () => setState(() { _future = _load(q); }));
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Projects'),
        scrolledUnderElevation: 1,
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(58),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
            child: TextField(
              controller: _search,
              onChanged: _onSearch,
              decoration: InputDecoration(
                hintText: 'Search projects',
                prefixIcon: const Icon(Icons.search, size: 20),
                isDense: true,
                filled: true,
                fillColor: kBg,
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: const BorderSide(color: kLine)),
                enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: const BorderSide(color: kLine)),
              ),
            ),
          ),
        ),
      ),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => setState(() { _future = _load(_search.text); }),
        child: FutureBuilder<List<Project>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const _JobsSkeleton();
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 120),
                const Icon(Icons.cloud_off, size: 44, color: kMuted),
                const SizedBox(height: 12),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
              ]);
            }
            final rows = snap.data ?? const [];
            if (rows.isEmpty) {
              return ListView(children: [
                const SizedBox(height: 120),
                const Icon(Icons.work_outline, size: 46, color: kMuted),
                const SizedBox(height: 12),
                Center(
                    child: Text(_search.text.isEmpty
                        ? 'No projects yet.'
                        : 'No projects match “${_search.text}”.')),
              ]);
            }
            return ListView.builder(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
              itemCount: rows.length,
              itemBuilder: (context, i) => _JobCard(
                project: rows[i],
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) =>
                        ProjectDetailScreen(api: widget.api, project: rows[i]))),
              ),
            );
          },
        ),
      ),
    );
  }
}

class _JobCard extends StatelessWidget {
  const _JobCard({required this.project, required this.onTap});
  final Project project;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final sub = [project.clientName, project.site]
        .where((s) => s.isNotEmpty)
        .join('  ·  ');
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: Container(
            decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: kLine)),
            padding: const EdgeInsets.all(15),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(
                  child: Text(project.title.isEmpty ? project.number : project.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          fontSize: 15.5, fontWeight: FontWeight.w600, color: kInk)),
                ),
                const SizedBox(width: 8),
                StatusChip(status: project.status),
              ]),
              const SizedBox(height: 5),
              Row(children: [
                const Icon(Icons.business, size: 13, color: kMuted),
                const SizedBox(width: 5),
                Expanded(
                  child: Text(sub.isEmpty ? project.number : sub,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 12.5, color: kMuted)),
                ),
                if (project.workType.isNotEmpty) ...[
                  const SizedBox(width: 8),
                  Text(project.workType,
                      style: const TextStyle(
                          fontSize: 11.5, color: kBrandDark, fontWeight: FontWeight.w600)),
                ],
              ]),
            ]),
          ),
        ),
      ),
    );
  }
}

/// Project-status pill (shared with Home). Tinted, colour communicates state.
class StatusChip extends StatelessWidget {
  const StatusChip({super.key, required this.status});
  final String status;

  @override
  Widget build(BuildContext context) {
    final (Color c, String label) = switch (status) {
      'ready' => (kGreen, 'Ready'),
      'in_execution' => (kInfo, 'In execution'),
      'complete' => (kMuted, 'Complete'),
      'cancelled' => (kRed, 'Cancelled'),
      _ => (kOrange, 'Pending'),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(
          color: c.withOpacity(0.13), borderRadius: BorderRadius.circular(8)),
      child: Text(label,
          style: TextStyle(color: c, fontSize: 11.5, fontWeight: FontWeight.w600)),
    );
  }
}

class _JobsSkeleton extends StatelessWidget {
  const _JobsSkeleton();
  @override
  Widget build(BuildContext context) {
    Widget card() => Container(
          margin: const EdgeInsets.only(bottom: 10),
          height: 78,
          decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: kLine)),
          padding: const EdgeInsets.all(15),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Container(width: 180, height: 15,
                decoration: BoxDecoration(color: kLine, borderRadius: BorderRadius.circular(6))),
            const SizedBox(height: 12),
            Container(width: 130, height: 12,
                decoration: BoxDecoration(color: kLine, borderRadius: BorderRadius.circular(6))),
          ]),
        );
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
      children: List.generate(5, (_) => card()),
    );
  }
}

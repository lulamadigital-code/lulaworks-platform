import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';
import 'project_detail_screen.dart';

class ProjectsScreen extends StatefulWidget {
  const ProjectsScreen({super.key, required this.api, required this.onSignOut});
  final ApiClient api;
  final Future<void> Function() onSignOut;

  @override
  State<ProjectsScreen> createState() => _ProjectsScreenState();
}

class _ProjectsScreenState extends State<ProjectsScreen> {
  late Future<List<Project>> _future = _load();

  Future<List<Project>> _load() async {
    final body = await widget.api.get('/projects/');
    return pageResults(body).map(Project.fromJson).toList();
  }

  Future<void> _refresh() async {
    setState(() { _future = _load(); });
    await _future;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Projects'),
        actions: [
          IconButton(
            tooltip: 'Sign out',
            icon: const Icon(Icons.logout),
            onPressed: widget.onSignOut,
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: FutureBuilder<List<Project>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return _ErrorView(error: snap.error, onRetry: _refresh);
            }
            final projects = snap.data ?? const [];
            if (projects.isEmpty) {
              return ListView(children: const [
                SizedBox(height: 120),
                Center(child: Text('No projects yet.')),
              ]);
            }
            return ListView.separated(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: projects.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) => _ProjectTile(
                project: projects[i],
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) =>
                      ProjectDetailScreen(api: widget.api, project: projects[i]),
                )),
              ),
            );
          },
        ),
      ),
    );
  }
}

class _ProjectTile extends StatelessWidget {
  const _ProjectTile({required this.project, required this.onTap});
  final Project project;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: CircleAvatar(
        backgroundColor: project.isReady
            ? Colors.green.shade100
            : Theme.of(context).colorScheme.surfaceVariant,
        child: Icon(project.isReady ? Icons.check : Icons.hourglass_bottom,
            color: project.isReady ? Colors.green.shade800 : null, size: 20),
      ),
      title: Text(project.title.isEmpty ? project.number : project.title),
      subtitle: Text('${project.number} · ${project.clientName}'
          '${project.site.isNotEmpty ? ' · ${project.site}' : ''}'),
      trailing: StatusChip(status: project.status),
      onTap: onTap,
    );
  }
}

class StatusChip extends StatelessWidget {
  const StatusChip({super.key, required this.status});
  final String status;

  @override
  Widget build(BuildContext context) {
    final (Color bg, Color fg, String label) = switch (status) {
      'ready' => (Colors.green.shade100, Colors.green.shade900, 'Ready'),
      'in_execution' => (Colors.blue.shade100, Colors.blue.shade900, 'In execution'),
      'complete' => (Colors.grey.shade300, Colors.grey.shade800, 'Complete'),
      'cancelled' => (Colors.red.shade100, Colors.red.shade900, 'Cancelled'),
      _ => (Colors.orange.shade100, Colors.orange.shade900, 'Pending compliance'),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(12)),
      child: Text(label, style: TextStyle(color: fg, fontSize: 12, fontWeight: FontWeight.w600)),
    );
  }
}

class _ErrorView extends StatelessWidget {
  const _ErrorView({required this.error, required this.onRetry});
  final Object? error;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    return ListView(children: [
      const SizedBox(height: 100),
      Icon(Icons.cloud_off, size: 48, color: Theme.of(context).colorScheme.outline),
      const SizedBox(height: 12),
      Center(child: Text('$error', textAlign: TextAlign.center)),
      const SizedBox(height: 16),
      Center(child: OutlinedButton(onPressed: onRetry, child: const Text('Retry'))),
    ]);
  }
}

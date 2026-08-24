import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models.dart';

/// Team management — the users.invite surface. Lists the active company's
/// members and lets an admin invite by email + role, change a member's role, or
/// remove them. The backend enforces users.invite on every write.
class TeamScreen extends StatefulWidget {
  const TeamScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<TeamScreen> createState() => _TeamScreenState();
}

class _TeamScreenState extends State<TeamScreen> {
  late Future<_TeamData> _future = _load();

  Future<_TeamData> _load() async {
    final results = await Future.wait([
      widget.api.get('/users/'),
      widget.api.get('/roles/'),
    ]);
    return _TeamData(
      members: pageResults(results[0]),
      roles: pageResults(results[1]),
    );
  }

  void _reload() => setState(() { _future = _load(); });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Team')),
      body: RefreshIndicator(
        onRefresh: () async => _reload(),
        child: FutureBuilder<_TeamData>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 100),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
              ]);
            }
            final d = snap.data!;
            return ListView(
              padding: const EdgeInsets.symmetric(vertical: 8),
              children: [
                for (final m in d.members) _memberTile(context, m, d.roles),
                if (d.members.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(24),
                    child: Center(child: Text('No members yet.')),
                  ),
              ],
            );
          },
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () async {
          final d = await _future;
          if (mounted) _openInvite(d.roles);
        },
        icon: const Icon(Icons.person_add),
        label: const Text('Invite'),
      ),
    );
  }

  Widget _memberTile(
      BuildContext context, Map<String, dynamic> m, List<Map<String, dynamic>> roles) {
    final user = (m['user'] as Map?)?.cast<String, dynamic>() ?? const {};
    final name = '${user['full_name'] ?? ''}'.trim();
    final email = '${user['email'] ?? ''}';
    return ListTile(
      leading: CircleAvatar(child: Text(_initials(name.isEmpty ? email : name))),
      title: Text(name.isEmpty ? email : name),
      subtitle: Text([
        '${m['role_name'] ?? '—'}',
        if ('${m['job_title'] ?? ''}'.isNotEmpty) '${m['job_title']}',
        if ('${m['status'] ?? ''}'.isNotEmpty) '${m['status']}',
      ].join(' · ')),
      trailing: PopupMenuButton<String>(
        onSelected: (v) {
          if (v == 'role') _openChangeRole(m, roles);
          if (v == 'remove') _confirmRemove(m, name.isEmpty ? email : name);
        },
        itemBuilder: (_) => const [
          PopupMenuItem(value: 'role', child: Text('Change role')),
          PopupMenuItem(value: 'remove', child: Text('Remove')),
        ],
      ),
    );
  }

  // ── Invite ────────────────────────────────────────────────────────────────
  void _openInvite(List<Map<String, dynamic>> roles) {
    final email = TextEditingController();
    final first = TextEditingController();
    final last = TextEditingController();
    final jobTitle = TextEditingController();
    String? roleId = roles.isNotEmpty ? '${roles.first['id']}' : null;

    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (sheetCtx) => Padding(
        padding: EdgeInsets.only(
            left: 20,
            right: 20,
            top: 4,
            bottom: MediaQuery.of(sheetCtx).viewInsets.bottom + 20),
        child: StatefulBuilder(
          builder: (ctx, setSheet) => Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('Invite a team member',
                  style: Theme.of(ctx).textTheme.titleMedium),
              const SizedBox(height: 12),
              TextField(
                controller: email,
                keyboardType: TextInputType.emailAddress,
                decoration: const InputDecoration(
                    labelText: 'Email', border: OutlineInputBorder()),
              ),
              const SizedBox(height: 10),
              Row(children: [
                Expanded(
                  child: TextField(
                    controller: first,
                    decoration: const InputDecoration(
                        labelText: 'First name', border: OutlineInputBorder()),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: TextField(
                    controller: last,
                    decoration: const InputDecoration(
                        labelText: 'Last name', border: OutlineInputBorder()),
                  ),
                ),
              ]),
              const SizedBox(height: 10),
              DropdownButtonFormField<String>(
                value: roleId,
                decoration: const InputDecoration(
                    labelText: 'Role', border: OutlineInputBorder()),
                items: [
                  for (final r in roles)
                    DropdownMenuItem(
                        value: '${r['id']}', child: Text('${r['name']}')),
                ],
                onChanged: (v) => setSheet(() => roleId = v),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: jobTitle,
                decoration: const InputDecoration(
                    labelText: 'Job title (optional)',
                    border: OutlineInputBorder()),
              ),
              const SizedBox(height: 16),
              FilledButton(
                onPressed: () async {
                  if (email.text.trim().isEmpty || roleId == null) return;
                  Navigator.pop(sheetCtx);
                  await _invite({
                    'email': email.text.trim(),
                    'first_name': first.text.trim(),
                    'last_name': last.text.trim(),
                    'role': roleId,
                    'job_title': jobTitle.text.trim(),
                  });
                },
                child: const Text('Send invite'),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _invite(Map<String, dynamic> body) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('/users/', body);
      _reload();
      messenger.showSnackBar(SnackBar(content: Text('Invited ${body['email']}')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission to invite members."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  // ── Change role ─────────────────────────────────────────────────────────────
  void _openChangeRole(
      Map<String, dynamic> m, List<Map<String, dynamic>> roles) {
    String? roleId = '${m['role']}';
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Change role'),
        content: StatefulBuilder(
          builder: (c, setD) => DropdownButtonFormField<String>(
            value: roles.any((r) => '${r['id']}' == roleId) ? roleId : null,
            decoration: const InputDecoration(labelText: 'Role'),
            items: [
              for (final r in roles)
                DropdownMenuItem(value: '${r['id']}', child: Text('${r['name']}')),
            ],
            onChanged: (v) => setD(() => roleId = v),
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            onPressed: () {
              Navigator.pop(ctx);
              _patchMember('${m['id']}', {'role': roleId});
            },
            child: const Text('Save'),
          ),
        ],
      ),
    );
  }

  Future<void> _patchMember(String id, Map<String, dynamic> body) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.patch('/users/$id/', body);
      _reload();
      messenger.showSnackBar(const SnackBar(content: Text('Role updated')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission to change roles."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  // ── Remove ──────────────────────────────────────────────────────────────────
  void _confirmRemove(Map<String, dynamic> m, String label) {
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Remove member?'),
        content: Text('$label will lose access to this company.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(
                backgroundColor: Theme.of(ctx).colorScheme.error),
            onPressed: () {
              Navigator.pop(ctx);
              _remove('${m['id']}');
            },
            child: const Text('Remove'),
          ),
        ],
      ),
    );
  }

  Future<void> _remove(String id) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.delete('/users/$id/');
      _reload();
      messenger.showSnackBar(const SnackBar(content: Text('Member removed')));
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(e.isForbidden
              ? "You don't have permission to remove members."
              : e.message)));
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not reach the server.')));
    }
  }

  String _initials(String s) {
    final parts = s.trim().split(RegExp(r'\s+')).where((p) => p.isNotEmpty).toList();
    if (parts.isEmpty) return '?';
    if (parts.length == 1) return parts.first.characters.first.toUpperCase();
    return (parts.first.characters.first + parts.last.characters.first)
        .toUpperCase();
  }
}

class _TeamData {
  _TeamData({required this.members, required this.roles});
  final List<Map<String, dynamic>> members;
  final List<Map<String, dynamic>> roles;
}

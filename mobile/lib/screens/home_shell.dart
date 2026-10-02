import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../api/sync_service.dart';
import '../nav/app_nav.dart';
import 'lulaai_screen.dart';

/// The signed-in app shell. The bottom bar is built entirely from the central,
/// permission-driven navigation config ([bottomTabsFor]) — different users get
/// a bar shaped for their job, with no role checks living here.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key, required this.api, required this.onSignOut});
  final ApiClient api;
  final Future<void> Function() onSignOut;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> with WidgetsBindingObserver {
  int _index = 0;
  WebSocket? _notifWs;
  bool _disposed = false;
  late final SyncService _sync = SyncService(widget.api);

  late final NavActions _actions = NavActions(
    onSignOut: widget.onSignOut,
    openProjects: () => _goto('jobs'),
    openLulaAi: _openLulaAi,
  );

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Resolve role/permissions on launch so the bar (and every gated surface)
    // is correct. If they changed (e.g. an admin adjusted this user's role),
    // the next refresh rebuilds the bar — no reinstall needed.
    widget.api.refreshMe().then((_) {
      if (mounted) setState(() {});
    }).catchError((_) {});
    _connectNotifs();
    _sync.start(); // coordinate the offline outboxes + flush on reconnect
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Coming back to the foreground is a good moment to push anything queued.
    if (state == AppLifecycleState.resumed) _sync.flushAll();
  }

  @override
  void dispose() {
    _disposed = true;
    WidgetsBinding.instance.removeObserver(this);
    _sync.dispose();
    _notifWs?.close();
    super.dispose();
  }

  /// Live notification badge: the server pushes the unread count over this
  /// socket whenever a notification is created or read, so the bell updates
  /// without any refresh or navigation.
  Future<void> _connectNotifs() async {
    if (_disposed) return;
    try {
      final uri = widget.api.wsUri('/ws/notifications/');
      final ws = await WebSocket.connect(uri.toString(),
              headers: {'Origin': widget.api.origin})
          .timeout(const Duration(seconds: 8));
      if (_disposed) {
        ws.close();
        return;
      }
      _notifWs = ws;
      ws.listen((data) {
        try {
          final f = jsonDecode('$data');
          if (f is Map && f['type'] == 'count' && f['count'] is int) {
            widget.api.unread.value = f['count'] as int;
          }
        } catch (_) {/* ignore */}
      },
          onDone: _notifClosed,
          onError: (_) => _notifClosed(),
          cancelOnError: true);
    } catch (_) {
      if (!_disposed) Future.delayed(const Duration(seconds: 6), _connectNotifs);
    }
  }

  void _notifClosed() {
    _notifWs = null;
    if (!_disposed) Future.delayed(const Duration(seconds: 6), _connectNotifs);
  }

  /// Switch to a tab by id (used by in-app shortcuts, e.g. the dashboard).
  /// No-op if that tab isn't part of this user's bar.
  void _goto(String id) {
    final i = bottomTabsFor(widget.api).indexWhere((t) => t.id == id);
    if (i >= 0) setState(() => _index = i);
  }

  void _openLulaAi() => Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => LulaAiScreen(api: widget.api)));

  @override
  Widget build(BuildContext context) {
    final tabs = bottomTabsFor(widget.api);
    // Guard against a shrinking bar (permissions resolved after first paint).
    final index = _index.clamp(0, tabs.length - 1);
    // Adaptive navigation (§20/§38): a side rail on tablets/landscape, the bottom
    // bar on phones. Same destinations, shaped to the screen.
    final wide = MediaQuery.of(context).size.width >= 720;
    final content = IndexedStack(
      index: index,
      children: [for (final t in tabs) t.build(widget.api, _actions)],
    );

    if (wide) {
      return Scaffold(
        body: Column(children: [
          _SyncBanner(sync: _sync),
          Expanded(
            child: Row(children: [
              NavigationRail(
                selectedIndex: index,
                onDestinationSelected: (i) => setState(() => _index = i),
                labelType: NavigationRailLabelType.all,
                destinations: [
                  for (final t in tabs)
                    NavigationRailDestination(
                        icon: Icon(t.icon),
                        selectedIcon: Icon(t.activeIcon),
                        label: Text(t.label)),
                ],
              ),
              const VerticalDivider(width: 1),
              Expanded(child: content),
            ]),
          ),
        ]),
      );
    }

    return Scaffold(
      body: Column(children: [
        _SyncBanner(sync: _sync),
        Expanded(child: content),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: [
          for (final t in tabs)
            NavigationDestination(
                icon: Icon(t.icon),
                selectedIcon: Icon(t.activeIcon),
                label: t.label),
        ],
      ),
    );
  }
}

/// A slim status strip shown only when offline or when field work is waiting to
/// sync. Tapping it retries. Bound to the SyncService's live notifiers, so it
/// appears/disappears on its own as connectivity and the outbox change.
class _SyncBanner extends StatelessWidget {
  const _SyncBanner({required this.sync});
  final SyncService sync;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: Listenable.merge([sync.online, sync.pending]),
      builder: (context, _) {
        final offline = !sync.online.value;
        final queued = sync.pending.value;
        if (!offline && queued == 0) return const SizedBox.shrink();
        final color = offline ? const Color(0xFF8A6D0B) : const Color(0xFF17a2b8);
        final bg = offline ? const Color(0xFFFFF7E0) : const Color(0xFFE6F6F9);
        final text = offline
            ? (queued > 0
                ? "Offline — $queued ${queued == 1 ? 'item' : 'items'} will sync when you reconnect"
                : 'Offline — changes are saved on this device')
            : "Syncing $queued ${queued == 1 ? 'item' : 'items'}…";
        return Material(
          color: bg,
          child: InkWell(
            onTap: offline ? null : () => sync.flushAll(),
            child: SafeArea(
              bottom: false,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 7),
                child: Row(children: [
                  Icon(offline ? Icons.cloud_off : Icons.cloud_sync,
                      size: 16, color: color),
                  const SizedBox(width: 8),
                  Expanded(
                      child: Text(text,
                          style: TextStyle(fontSize: 12.5, color: color),
                          maxLines: 1, overflow: TextOverflow.ellipsis)),
                  if (!offline && queued > 0)
                    Text('Retry',
                        style: TextStyle(
                            fontSize: 12.5,
                            fontWeight: FontWeight.w700,
                            color: color)),
                ]),
              ),
            ),
          ),
        );
      },
    );
  }
}

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../api/api_client.dart';
import '../api/attendance_store.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';

/// Time & Attendance — the worker clocks in/out, takes breaks, and sees today at
/// a glance. Event-based (never a live trail). Works offline: an event is queued
/// with its real device time and synced when the network returns.
class AttendanceScreen extends StatefulWidget {
  const AttendanceScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<AttendanceScreen> createState() => _AttendanceScreenState();
}

class _AttendanceScreenState extends State<AttendanceScreen> {
  final _store = AttendanceStore();
  late Future<Map<String, dynamic>> _future = _load();
  Timer? _ticker;
  bool _busy = false;
  int _pending = 0;
  DateTime _loadedAt = DateTime.now();

  @override
  void initState() {
    super.initState();
    // Tick every second so the elapsed clock moves while working.
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  Future<Map<String, dynamic>> _load() async {
    // Best-effort sync of anything captured offline, then read today.
    try {
      await _store.flush(widget.api);
    } catch (_) {/* stay offline-friendly */}
    _pending = await _store.pendingCount();
    final data = await widget.api.get('/attendance-events/today/')
        as Map<String, dynamic>;
    _loadedAt = DateTime.now();
    return data;
  }

  void _reload() => setState(() { _future = _load(); });

  Future<Position?> _location() async {
    try {
      if (!await Geolocator.isLocationServiceEnabled()) return null;
      var perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.denied ||
          perm == LocationPermission.deniedForever) return null;
      return await Geolocator.getCurrentPosition(
          desiredAccuracy: LocationAccuracy.high);
    } catch (_) {
      return null;
    }
  }

  Future<void> _record(String kind, {bool withGps = false}) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = true);
    try {
      final pos = withGps ? await _location() : null;
      final body = <String, dynamic>{
        'kind': kind,
        'occurred_at': DateTime.now().toUtc().toIso8601String(),
        if (pos != null) 'latitude': pos.latitude.toStringAsFixed(6),
        if (pos != null) 'longitude': pos.longitude.toStringAsFixed(6),
      };
      try {
        await widget.api.post('/attendance-events/', body);
        messenger.showSnackBar(SnackBar(content: Text(_verb(kind))));
      } catch (e) {
        if (e is ApiException) rethrow; // real server rejection — surface it
        // Offline: queue it, it will sync later with its real time.
        await _store.enqueue(body);
        messenger.showSnackBar(SnackBar(
            content: Text('${_verb(kind)} — saved offline, will sync.')));
      }
      _reload();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _verb(String kind) => switch (kind) {
        'clock_in' => 'Clocked in',
        'clock_out' => 'Clocked out',
        'break_start' => 'Break started',
        'break_end' => 'Break ended',
        _ => 'Recorded',
      };

  Future<void> _requestCorrection() async {
    final noteC = TextEditingController();
    String kind = 'clock_in';
    DateTime when = DateTime.now();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSt) => AlertDialog(
          title: const Text('Request a correction'),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            LulaDropdown<String>(
              label: 'Event',
              value: kind,
              items: const [
                DropdownMenuItem(value: 'clock_in', child: Text('Clock in')),
                DropdownMenuItem(value: 'clock_out', child: Text('Clock out')),
                DropdownMenuItem(value: 'break_start', child: Text('Break start')),
                DropdownMenuItem(value: 'break_end', child: Text('Break end')),
              ],
              onChanged: (v) => setSt(() => kind = v ?? 'clock_in'),
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              icon: const Icon(Icons.schedule, size: 18),
              label: Text('${when.hour.toString().padLeft(2, '0')}:'
                  '${when.minute.toString().padLeft(2, '0')}  ·  '
                  '${when.day}/${when.month}'),
              onPressed: () async {
                final t = await showTimePicker(
                    context: ctx, initialTime: TimeOfDay.fromDateTime(when));
                if (t != null) {
                  setSt(() => when = DateTime(when.year, when.month, when.day,
                      t.hour, t.minute));
                }
              },
            ),
            const SizedBox(height: 12),
            LulaTextField(controller: noteC, label: 'Reason', maxLines: 2),
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Submit')),
          ],
        ),
      ),
    );
    if (ok != true || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.post('/attendance-events/', {
        'kind': kind,
        'occurred_at': when.toUtc().toIso8601String(),
        'is_correction': true,
        'note': noteC.text.trim(),
      });
      messenger.showSnackBar(const SnackBar(
          content: Text('Correction sent to your manager for review.')));
      _reload();
    } catch (_) {
      messenger.showSnackBar(
          const SnackBar(content: Text('Could not submit — try again.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Time & attendance'), scrolledUnderElevation: 1),
      body: RefreshIndicator(
        color: kBrand,
        onRefresh: () async => _reload(),
        child: FutureBuilder<Map<String, dynamic>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator(color: kBrand));
            }
            if (snap.hasError) {
              return ListView(children: [
                const SizedBox(height: 120),
                const Icon(Icons.cloud_off, size: 44, color: kMuted),
                const SizedBox(height: 12),
                Center(child: Text('${snap.error}', textAlign: TextAlign.center)),
              ]);
            }
            return _content(context, snap.data!);
          },
        ),
      ),
    );
  }

  Widget _content(BuildContext context, Map<String, dynamic> d) {
    final summary = (d['summary'] as Map).cast<String, dynamic>();
    final events = (d['events'] as List? ?? const []).cast<Map<String, dynamic>>();
    final state = '${summary['state']}';
    final worked = _liveWorked(summary);

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
      children: [
        if (_pending > 0) _offlineBanner(),
        _clockCard(context, state, worked, summary),
        const SizedBox(height: 24),
        Row(children: [
          Expanded(child: _sectionTitle('Today')),
          TextButton.icon(
              onPressed: _requestCorrection,
              icon: const Icon(Icons.edit_calendar_outlined, size: 18),
              label: const Text('Correction')),
        ]),
        const SizedBox(height: 8),
        if (events.isEmpty)
          _card(const Text('No events yet today. Clock in to start.',
              style: TextStyle(fontSize: 13, color: kMuted)))
        else
          _card(Column(children: [
            for (int i = 0; i < events.length; i++) ...[
              if (i > 0) const Divider(height: 1),
              _eventRow(events[i]),
            ],
          ])),
      ],
    );
  }

  // Worked seconds, ticking live while working (server value at load + elapsed).
  int _liveWorked(Map<String, dynamic> summary) {
    final base = (summary['worked_seconds'] as int? ?? 0);
    if ('${summary['state']}' == 'working') {
      return base + DateTime.now().difference(_loadedAt).inSeconds;
    }
    return base;
  }

  Widget _clockCard(BuildContext context, String state, int worked,
      Map<String, dynamic> summary) {
    final working = state == 'working';
    final onBreak = state == 'on_break';
    final (Color c, String label, IconData icon) = onBreak
        ? (kOrange, 'On break', Icons.free_breakfast_outlined)
        : working
            ? (kGreen, 'Working', Icons.work_outline)
            : (kMuted, 'Clocked out', Icons.bedtime_outlined);
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: kLine)),
      padding: const EdgeInsets.all(20),
      child: Column(children: [
        Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          Icon(icon, color: c, size: 18),
          const SizedBox(width: 6),
          Text(label,
              style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: c)),
        ]),
        const SizedBox(height: 12),
        Text(_hms(worked),
            style: const TextStyle(
                fontSize: 40, fontWeight: FontWeight.w800, color: kInk,
                letterSpacing: -1, fontFeatures: [])),
        const Text('worked today', style: TextStyle(fontSize: 12.5, color: kMuted)),
        const SizedBox(height: 20),
        if (state == 'clocked_out')
          _bigButton('Clock in', Icons.login, kGreen,
              () => _record('clock_in', withGps: true))
        else ...[
          Row(children: [
            Expanded(
              child: _bigButton(
                  onBreak ? 'End break' : 'Start break',
                  onBreak ? Icons.play_arrow : Icons.pause,
                  kOrange,
                  () => _record(onBreak ? 'break_end' : 'break_start')),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _bigButton('Clock out', Icons.logout, kRed,
                  () => _record('clock_out', withGps: true)),
            ),
          ]),
        ],
      ]),
    );
  }

  Widget _bigButton(String label, IconData icon, Color color, VoidCallback onTap) {
    return SizedBox(
      height: 52,
      child: FilledButton.icon(
        onPressed: _busy ? null : onTap,
        icon: _busy
            ? const SizedBox(width: 18, height: 18,
                child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
            : Icon(icon),
        label: Text(label, style: const TextStyle(fontSize: 15)),
        style: FilledButton.styleFrom(backgroundColor: color),
      ),
    );
  }

  Widget _offlineBanner() => Container(
        margin: const EdgeInsets.only(bottom: 14),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
            color: kOrange.withOpacity(0.10),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: kOrange.withOpacity(0.3))),
        child: Row(children: [
          const Icon(Icons.sync, color: kOrange, size: 18),
          const SizedBox(width: 10),
          Expanded(
            child: Text('$_pending event${_pending == 1 ? '' : 's'} saved offline — '
                'pending sync.',
                style: const TextStyle(fontSize: 13, color: kInk)),
          ),
        ]),
      );

  Widget _eventRow(Map<String, dynamic> e) {
    final when = DateTime.tryParse('${e['occurred_at']}')?.toLocal();
    final kind = '${e['kind']}';
    final pending = '${e['status']}' == 'pending';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 11),
      child: Row(children: [
        Icon(_iconFor(kind), size: 20, color: _colorFor(kind)),
        const SizedBox(width: 12),
        Expanded(
          child: Text('${e['kind_display'] ?? kind}',
              style: const TextStyle(fontSize: 14.5, color: kInk)),
        ),
        if (pending)
          Container(
            margin: const EdgeInsets.only(right: 8),
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(
                color: kOrange.withOpacity(0.13),
                borderRadius: BorderRadius.circular(6)),
            child: const Text('Pending review',
                style: TextStyle(fontSize: 10.5, color: kOrange, fontWeight: FontWeight.w700)),
          ),
        Text(
            when == null
                ? ''
                : '${when.hour.toString().padLeft(2, '0')}:'
                    '${when.minute.toString().padLeft(2, '0')}',
            style: const TextStyle(
                fontSize: 14, fontWeight: FontWeight.w700, color: kInk)),
      ]),
    );
  }

  IconData _iconFor(String k) => switch (k) {
        'clock_in' => Icons.login,
        'clock_out' => Icons.logout,
        'break_start' => Icons.pause_circle_outline,
        'break_end' => Icons.play_circle_outline,
        'site_arrival' => Icons.place,
        'site_departure' => Icons.flag_outlined,
        _ => Icons.schedule,
      };

  Color _colorFor(String k) => switch (k) {
        'clock_in' || 'break_end' => kGreen,
        'clock_out' => kRed,
        'break_start' => kOrange,
        _ => kBrand,
      };

  String _hms(int s) {
    final h = s ~/ 3600, m = (s % 3600) ~/ 60, sec = s % 60;
    return '${h.toString().padLeft(2, '0')}:${m.toString().padLeft(2, '0')}:'
        '${sec.toString().padLeft(2, '0')}';
  }

  Widget _card(Widget child) => Container(
        width: double.infinity,
        decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: kLine)),
        padding: const EdgeInsets.all(16),
        child: child,
      );

  Widget _sectionTitle(String t) => Text(t.toUpperCase(),
      style: const TextStyle(
          fontSize: 11.5, fontWeight: FontWeight.w700,
          letterSpacing: 0.6, color: kMuted));
}


/// A compact clock strip for the Field Home — shows the current state + elapsed
/// and a one-tap Clock in / Clock out. Tapping the card opens the full screen.
class AttendanceClockStrip extends StatefulWidget {
  const AttendanceClockStrip({super.key, required this.api});
  final ApiClient api;

  @override
  State<AttendanceClockStrip> createState() => _AttendanceClockStripState();
}

class _AttendanceClockStripState extends State<AttendanceClockStrip> {
  final _store = AttendanceStore();
  Map<String, dynamic>? _summary;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      await _store.flush(widget.api);
      final d = await widget.api.get('/attendance-events/today/') as Map;
      if (mounted) setState(() => _summary = (d['summary'] as Map).cast<String, dynamic>());
    } catch (_) {/* leave strip in its last state */}
  }

  Future<void> _quick(String kind) async {
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    final body = {'kind': kind, 'occurred_at': DateTime.now().toUtc().toIso8601String()};
    try {
      try {
        await widget.api.post('/attendance-events/', body);
      } catch (e) {
        if (e is ApiException) rethrow;
        await _store.enqueue(body);
        messenger.showSnackBar(const SnackBar(content: Text('Saved offline, will sync.')));
      }
      await _load();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _openFull() async {
    await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => AttendanceScreen(api: widget.api)));
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final s = _summary;
    final state = '${s?['state'] ?? 'clocked_out'}';
    final working = state == 'working';
    final onBreak = state == 'on_break';
    final worked = (s?['worked_seconds'] as int? ?? 0);
    final (Color c, String label) = onBreak
        ? (kOrange, 'On break')
        : working
            ? (kGreen, 'Working · ${_hm(worked)}')
            : (kMuted, 'Not clocked in');
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: _openFull,
        child: Container(
          decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: kLine)),
          padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
          child: Row(children: [
            Container(width: 10, height: 10,
                decoration: BoxDecoration(color: c, shape: BoxShape.circle)),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Text('Time & attendance',
                    style: TextStyle(fontSize: 12.5, color: kMuted)),
                Text(label,
                    style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: c)),
              ]),
            ),
            const SizedBox(width: 8),
            FilledButton(
              onPressed: _busy
                  ? null
                  : () => _quick(working || onBreak ? 'clock_out' : 'clock_in'),
              style: FilledButton.styleFrom(
                  backgroundColor: (working || onBreak) ? kRed : kGreen,
                  padding: const EdgeInsets.symmetric(horizontal: 16)),
              child: Text(working || onBreak ? 'Clock out' : 'Clock in'),
            ),
          ]),
        ),
      ),
    );
  }

  String _hm(int s) =>
      '${(s ~/ 3600).toString().padLeft(2, '0')}h '
      '${((s % 3600) ~/ 60).toString().padLeft(2, '0')}m';
}

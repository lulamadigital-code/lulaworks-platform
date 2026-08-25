import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../theme.dart';

/// A small OpenStreetMap preview centred on a captured GPS point, with a marker.
/// No API key — free OSM tiles. Optionally shows a second (site) marker so a
/// field check-in can be seen relative to where it was supposed to happen.
class MiniMap extends StatelessWidget {
  const MiniMap({
    super.key,
    required this.lat,
    required this.lng,
    this.siteLat,
    this.siteLng,
    this.height = 160,
    this.pointColor = kBrand,
  });

  final double lat;
  final double lng;
  final double? siteLat;
  final double? siteLng;
  final double height;
  final Color pointColor;

  /// Build from dynamic (string/num) coords; returns null if either is missing.
  static MiniMap? tryFrom(dynamic lat, dynamic lng,
      {dynamic siteLat, dynamic siteLng, double height = 160, Color pointColor = kBrand}) {
    final a = double.tryParse('$lat');
    final b = double.tryParse('$lng');
    if (a == null || b == null) return null;
    return MiniMap(
      lat: a, lng: b,
      siteLat: double.tryParse('$siteLat'),
      siteLng: double.tryParse('$siteLng'),
      height: height, pointColor: pointColor,
    );
  }

  @override
  Widget build(BuildContext context) {
    final point = LatLng(lat, lng);
    final site = (siteLat != null && siteLng != null) ? LatLng(siteLat!, siteLng!) : null;
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: SizedBox(
        height: height,
        child: Stack(children: [
          FlutterMap(
            options: MapOptions(
              initialCenter: point,
              initialZoom: 15,
              interactionOptions: const InteractionOptions(
                  flags: InteractiveFlag.pinchZoom |
                      InteractiveFlag.drag |
                      InteractiveFlag.doubleTapZoom),
            ),
            children: [
              TileLayer(
                urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                userAgentPackageName: 'za.co.lulaworks.lulaworks_mobile',
              ),
              MarkerLayer(markers: [
                if (site != null)
                  Marker(point: site, width: 30, height: 30,
                      child: const Icon(Icons.place_outlined, color: kMuted, size: 26)),
                Marker(point: point, width: 42, height: 42,
                    child: Icon(Icons.location_on, color: pointColor, size: 38)),
              ]),
            ],
          ),
          // OSM attribution (required by their tile usage policy).
          Positioned(
            right: 4, bottom: 2,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
              color: Colors.white70,
              child: const Text('© OpenStreetMap',
                  style: TextStyle(fontSize: 9, color: kMuted)),
            ),
          ),
        ]),
      ),
    );
  }
}

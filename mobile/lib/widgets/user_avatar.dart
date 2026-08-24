import 'package:flutter/material.dart';

import '../theme.dart';

/// A user's profile photo, falling back to their initials on a branded tint
/// when there's no photo (or it fails to load).
class UserAvatar extends StatelessWidget {
  const UserAvatar({super.key, this.url, required this.name, this.radius = 22});
  final String? url;
  final String name;
  final double radius;

  String get _initials {
    final parts = name.trim().split(RegExp(r'[\s@.]')).where((s) => s.isNotEmpty);
    if (parts.isEmpty) return '?';
    final take = parts.take(2).map((s) => s.characters.first.toUpperCase());
    return take.join();
  }

  @override
  Widget build(BuildContext context) {
    final fallback = CircleAvatar(
      radius: radius,
      backgroundColor: kBrandTint,
      child: Text(_initials,
          style: TextStyle(
              color: kBrandDark,
              fontWeight: FontWeight.w700,
              fontSize: radius * 0.62)),
    );
    if (url == null || url!.isEmpty) return fallback;
    return CircleAvatar(
      radius: radius,
      backgroundColor: kBrandTint,
      child: ClipOval(
        child: Image.network(
          url!,
          width: radius * 2,
          height: radius * 2,
          fit: BoxFit.cover,
          errorBuilder: (_, __, ___) => fallback,
          loadingBuilder: (context, child, progress) =>
              progress == null ? child : fallback,
        ),
      ),
    );
  }
}

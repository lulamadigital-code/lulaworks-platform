import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../api/api_client.dart';
import '../theme.dart';
import '../widgets/lula_ui.dart';
import '../widgets/user_avatar.dart';

/// Edit the signed-in user's PERSONAL details only (name, mobile, job title,
/// photo). Company data lives elsewhere. PATCHes /me/ and uploads the avatar to
/// /me/avatar/; refreshes the cached identity on success.
class EditProfileScreen extends StatefulWidget {
  const EditProfileScreen({super.key, required this.api, required this.me});
  final ApiClient api;
  final Map<String, dynamic> me;

  @override
  State<EditProfileScreen> createState() => _EditProfileScreenState();
}

class _EditProfileScreenState extends State<EditProfileScreen> {
  late final Map<String, dynamic> _user =
      (widget.me['user'] as Map?)?.cast<String, dynamic>() ?? {};
  late final _first = TextEditingController(text: '${_user['first_name'] ?? ''}');
  late final _last = TextEditingController(text: '${_user['last_name'] ?? ''}');
  late final _mobile = TextEditingController(text: '${_user['mobile'] ?? ''}');
  late final _jobTitle =
      TextEditingController(text: '${widget.me['job_title'] ?? ''}');
  late String? _avatar = _user['avatar'] as String?;
  bool _saving = false;
  bool _photoBusy = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [_first, _last, _mobile, _jobTitle]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _changePhoto(ImageSource source) async {
    final picker = ImagePicker();
    final XFile? picked = await picker.pickImage(
        source: source, maxWidth: 1024, maxHeight: 1024, imageQuality: 85);
    if (picked == null) return;
    if (!mounted) return;
    setState(() => _photoBusy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final res = await widget.api.postMultipart('/me/avatar/',
          filePath: picked.path, fileField: 'file');
      String? url;
      if (res is Map && res['user'] is Map) {
        url = (res['user'] as Map)['avatar'] as String?;
      }
      await widget.api.refreshMe();
      if (mounted) setState(() => _avatar = url);
    } catch (_) {
      messenger.showSnackBar(const SnackBar(
          content: Text('Unable to update profile photo. Try again.')));
    } finally {
      if (mounted) setState(() => _photoBusy = false);
    }
  }

  Future<void> _removePhoto() async {
    setState(() => _photoBusy = true);
    try {
      await widget.api.delete('/me/avatar/');
      await widget.api.refreshMe();
      if (mounted) setState(() => _avatar = null);
    } catch (_) {/* keep current */} finally {
      if (mounted) setState(() => _photoBusy = false);
    }
  }

  void _photoSheet() {
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: const Icon(Icons.photo_camera_outlined),
            title: const Text('Take a photo'),
            onTap: () {
              Navigator.pop(ctx);
              _changePhoto(ImageSource.camera);
            },
          ),
          ListTile(
            leading: const Icon(Icons.photo_library_outlined),
            title: const Text('Choose from gallery'),
            onTap: () {
              Navigator.pop(ctx);
              _changePhoto(ImageSource.gallery);
            },
          ),
          if (_avatar != null)
            ListTile(
              leading: Icon(Icons.delete_outline, color: Theme.of(ctx).colorScheme.error),
              title: Text('Remove photo',
                  style: TextStyle(color: Theme.of(ctx).colorScheme.error)),
              onTap: () {
                Navigator.pop(ctx);
                _removePhoto();
              },
            ),
          const SizedBox(height: 8),
        ]),
      ),
    );
  }

  Future<void> _save() async {
    if (_first.text.trim().isEmpty) {
      setState(() => _error = 'A first name is required.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.patch('/me/', {
        'first_name': _first.text.trim(),
        'last_name': _last.text.trim(),
        'mobile': _mobile.text.trim(),
        'job_title': _jobTitle.text.trim(),
      });
      await widget.api.refreshMe();
      if (!mounted) return;
      messenger.showSnackBar(const SnackBar(content: Text('Profile saved')));
      Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Could not reach the server.');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final name = '${_first.text} ${_last.text}'.trim();
    return Scaffold(
      appBar: AppBar(title: const Text('Edit profile')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Center(
            child: Stack(children: [
              UserAvatar(
                  url: _avatar,
                  name: name.isEmpty ? '${_user['email'] ?? ''}' : name,
                  radius: 44),
              Positioned(
                right: 0,
                bottom: 0,
                child: Material(
                  color: kBrand,
                  shape: const CircleBorder(),
                  child: InkWell(
                    customBorder: const CircleBorder(),
                    onTap: _photoBusy ? null : _photoSheet,
                    child: Padding(
                      padding: const EdgeInsets.all(7),
                      child: _photoBusy
                          ? const SizedBox(
                              width: 16, height: 16,
                              child: CircularProgressIndicator(
                                  strokeWidth: 2, color: Colors.white))
                          : const Icon(Icons.camera_alt,
                              size: 16, color: Colors.white),
                    ),
                  ),
                ),
              ),
            ]),
          ),
          const SizedBox(height: 24),
          LulaTextField(
              controller: _first,
              label: 'First name',
              required: true,
              keyboardType: TextInputType.name,
              onChanged: (_) => setState(() {})),
          const SizedBox(height: 16),
          LulaTextField(
              controller: _last,
              label: 'Last name',
              keyboardType: TextInputType.name,
              onChanged: (_) => setState(() {})),
          const SizedBox(height: 16),
          LulaTextField(
              controller: _mobile, label: 'Mobile', keyboardType: TextInputType.phone),
          const SizedBox(height: 16),
          LulaTextField(controller: _jobTitle, label: 'Job title'),
          const SizedBox(height: 16),
          // Email is the login identifier — shown read-only.
          LulaTextField(
              controller: TextEditingController(text: '${_user['email'] ?? ''}'),
              label: 'Email (sign-in)',
              enabled: false),
          if (_error != null) ...[
            const SizedBox(height: 14),
            Text(_error!, style: const TextStyle(color: kRed, fontSize: 13)),
          ],
          const SizedBox(height: 22),
          LulaButton(
            label: 'Save changes',
            loadingLabel: 'Saving…',
            loading: _saving,
            onPressed: _save,
          ),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

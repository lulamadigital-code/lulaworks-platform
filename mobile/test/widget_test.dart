// Smoke test: the app boots to the login screen when unauthenticated.
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:lulaworks_mobile/api/api_client.dart';
import 'package:lulaworks_mobile/main.dart';

void main() {
  testWidgets('boots to the sign-in screen when unauthenticated', (tester) async {
    SharedPreferences.setMockInitialValues({});
    FlutterSecureStorage.setMockInitialValues({}); // no stored tokens
    final api = await ApiClient.create();
    await tester.pumpWidget(LulaworksApp(api: api));
    await tester.pumpAndSettle(); // let the first async frame resolve

    // The redesigned login screen shows "Welcome back" + a Sign in button.
    expect(find.text('Welcome back'), findsOneWidget);
    expect(find.text('Sign in'), findsWidgets);
  });
}

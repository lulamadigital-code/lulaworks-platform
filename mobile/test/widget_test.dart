// Smoke test: the app boots to the login screen when unauthenticated.
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:lulaworks_mobile/api/api_client.dart';
import 'package:lulaworks_mobile/main.dart';

void main() {
  testWidgets('boots to the sign-in screen when unauthenticated', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final api = await ApiClient.create();
    await tester.pumpWidget(LulaworksApp(api: api));
    await tester.pump();

    // The redesigned login screen shows "Welcome back" + a Sign in button.
    expect(find.text('Welcome back'), findsOneWidget);
    expect(find.text('Sign in'), findsWidgets);
  });
}

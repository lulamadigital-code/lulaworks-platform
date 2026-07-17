// Smoke test: the app boots to the login screen when unauthenticated.
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:lulaworks_mobile/api/api_client.dart';
import 'package:lulaworks_mobile/main.dart';

void main() {
  testWidgets('boots to the sign-in screen', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final api = await ApiClient.create();
    await tester.pumpWidget(LulaWorksApp(api: api));
    await tester.pumpAndSettle();

    expect(find.text('LulaWorks'), findsWidgets);
    expect(find.text('Sign in'), findsOneWidget);
  });
}

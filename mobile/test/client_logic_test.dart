import 'package:flutter_test/flutter_test.dart';
import 'package:lulaworks_mobile/api/api_client.dart';
import 'package:lulaworks_mobile/nav/deep_link.dart';

void main() {
  group('isDeepLinkable', () {
    const id = '11111111-2222-3333-4444-555555555555';

    test('maps known record paths', () {
      for (final t in ['quotations', 'customers', 'customer-pos', 'jobs',
          'commercial-documents', 'leads', 'projects']) {
        expect(isDeepLinkable('/$t/$id/'), isTrue, reason: t);
      }
    });

    test('tolerates host, query and missing trailing slash', () {
      expect(isDeepLinkable('https://www.lulaworks.com/quotations/$id'), isTrue);
      expect(isDeepLinkable('/quotations/$id/?tab=lines'), isTrue);
    });

    test('rejects unknown types, non-uuids, and empties', () {
      expect(isDeepLinkable('/marketing/$id/'), isFalse);
      expect(isDeepLinkable('/quotations/not-a-uuid/'), isFalse);
      expect(isDeepLinkable(''), isFalse);
      expect(isDeepLinkable(null), isFalse);
    });
  });

  group('newIdempotencyKey', () {
    test('is non-empty and unique across calls', () {
      final keys = <String>{};
      for (var i = 0; i < 1000; i++) {
        final k = newIdempotencyKey();
        expect(k, isNotEmpty);
        keys.add(k);
      }
      expect(keys.length, 1000); // no collisions
    });
  });
}

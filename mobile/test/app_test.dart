import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:reporttool_mobile/main.dart';

/// 測試在 mobile/ 底下執行，標準測試資料在 repo 根目錄的 tests/golden/。
final golden = Directory('../tests/golden');

void main() {
  testWidgets('首頁顯示標題與版本', (tester) async {
    await tester.pumpWidget(const ReportToolApp());
    expect(find.text('工作回報工具'), findsOneWidget);
    expect(find.text('版本 $appVersion'), findsOneWidget);
  });

  test('appVersion 和 pubspec.yaml 一致', () {
    final pubspec = File('pubspec.yaml').readAsStringSync();
    expect(pubspec, contains('version: $appVersion+'));
  });

  test('讀得到桌面版的標準測試資料', () {
    final bundle = jsonDecode(File('${golden.path}/basic/bundle.json').readAsStringSync()) as Map<String, dynamic>;
    expect(bundle['format'], isNotNull);
    expect((bundle['entries'] as List<dynamic>).length, 6);
    expect(File('${golden.path}/basic/expected_report.md').existsSync(), isTrue);
  });
}

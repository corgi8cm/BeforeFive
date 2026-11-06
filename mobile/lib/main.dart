import 'package:flutter/material.dart';

/// 和 pubspec.yaml 的 version 一致（測試會檢查）。
const appVersion = '0.1.0';

void main() {
  runApp(const ReportToolApp());
}

class ReportToolApp extends StatelessWidget {
  const ReportToolApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '工作回報工具',
      theme: ThemeData(colorSchemeSeed: const Color(0xFF2F6FD6), useMaterial3: true),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('工作回報工具')),
      body: const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.edit_note, size: 72),
            SizedBox(height: 16),
            Text('手機版開發中', style: TextStyle(fontSize: 20)),
            SizedBox(height: 8),
            Text('版本 $appVersion'),
          ],
        ),
      ),
    );
  }
}

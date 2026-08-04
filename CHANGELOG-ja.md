# 変更履歴

[English](./CHANGELOG.md)

主要な変更を記録します。本プロジェクトは
[Semantic Versioning](https://semver.org/lang/ja/)に従います。

## 1.0.1 - 2026-08-04

- 人間と自動化エージェント向けの初回セットアップRunbookを英日2版で追加しました。
- 検証処理が設定済みのDifyおよびmrag host portを参照するようにしました。

## 1.0.0 - 2026-08-04

最初の一般公開releaseです。

- Dify、mrag、Docling Serve、Ollama、専用Qdrantを統合しました。
- DoclingによるPDF、text、Office、画像取り込みに対応しました。
- 削除guard付き差分同期、dry-run、診断、バックアップ、復旧toolを追加しました。
- Linux、macOS、Windows操作に対応したCPU/NVIDIA profileを追加しました。
- 日本語全文検索とCrossEncoder rerankingを追加しました。

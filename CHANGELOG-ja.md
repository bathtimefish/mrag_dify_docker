# 変更履歴

[English](./CHANGELOG.md)

主要な変更を記録します。本プロジェクトは
[Semantic Versioning](https://semver.org/lang/ja/)に従います。

## Unreleased

- 新規インストール直後の同期が`PermissionError`で失敗する問題を修正しました。
  batchのentrypointがproject/backup volumeに加えて`mrag_converted_docs`
  volumeも`mrag`ユーザーへ引き渡すようにしました。
- 原本が既にMarkdownの場合に`.md`を二重付加しないようにしました。出典表示が
  `report.md.md`ではなく`report.md`になります。次回同期で変換済みMarkdownは改名
  されますが、indexは内容hashで管理するため、既にindex済みの文書は原本が変わるまで
  以前のtitleのままです。変換先名が他の原本と衝突する原本は変換せず
  `output_path_conflict`として記録します。

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

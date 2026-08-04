# 運用手順

[English](./operations.md)

## 状態確認

```bash
./scripts/compose.sh ps
./scripts/compose.sh logs --tail=100 mrag-api mrag-batch
./scripts/docling-status.sh
```

portを変更していない場合、Difyは<http://localhost/>、mrag API仕様は
<http://localhost:8000/docs>です。

## 同期

常に最初に計画を確認します。

```bash
./scripts/sync-dry-run.sh
./scripts/sync-now.sh
```

既定scheduleは設定timezoneで3時間ごとです。非blocking lockがcronと手動実行の重複を
防止します。

構造化logには`sync_plan`、`document_added`、`document_removed`、
`sync_completed`、`sync_failed`、`deletion_guard_warning`等があります。

## 削除guard

既定では20%以上または50件以上を削除する前に停止します。source bind mountとdry-runを
確認してからoverrideします。

```bash
./scripts/sync-now.sh --allow-large-delete
```

## 再indexと再変換

Embeddingまたはchunk設定を変えた後に再indexします。

```bash
./scripts/reindex-now.sh
```

変換動作変更後または変換済みMarkdownを再構築するときに再変換します。

```bash
./scripts/sync-dry-run.sh
./scripts/reconvert-now.sh
```

## Model管理

```bash
./scripts/ollama-model.sh list
./scripts/ollama-model.sh pull MODEL
./scripts/ollama-model.sh remove MODEL
```

profileが参照するmodelの削除は明示的にforceしない限り拒否します。Embedding model変更後は
再indexします。

## Backupと復元

full-stack backup toolを使い、復元前にarchiveを検証します。

```bash
./scripts/backup.sh --stop-services
./scripts/restore.sh --archive PATH
```

restoreは既定で計画表示だけです。実行前に[バックアップと復元](./backup-and-restore-ja.md)を
確認してください。

## 更新

`VERSIONS`、image lock、Notice、testを同時に変更するreview済み更新だけを適用します。
`.env`、profile、原本文書、volumeを保持し、`upgrade-env.sh`、build、Compose検証、起動、
verifyの順に実行します。

## 障害対応

[トラブルシューティング](./troubleshooting-ja.md)を使用します。通常の復旧でvolume削除、
Docker prune、`down -v`を実行しません。

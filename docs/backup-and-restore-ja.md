# バックアップと復元

[English](./backup-and-restore.md)

backup toolは設定済みstackの一貫したcold archiveを作成します。認証情報と運用dataを
含むため、Unixでは出力をmode `0600`で作成し、運用者が暗号化とaccess制限を行います。

## 収録対象

既定のbackup:

- root `.env`
- hostの`mrag_profiles/`
- Dify bind mount dataの`vendor/dify/docker/volumes/`
- Docker volumeの`mrag_data`、`mrag_qdrant_data`、`mrag_converted_docs`、
  `mrag_backups`
- version、volume名、SHA-256 checksumを持つJSON manifest

原本文書は`--include-documents`指定時だけ、再取得可能なOllama、reranker、Docling model
cacheは`--include-caches`指定時だけ収録します。

## Backup作成

```bash
./scripts/backup.sh --stop-services
```

service稼働中は`--stop-services`なしでは拒否します。command自身が停止したserviceは既存
containerのまま再起動し、backup処理中にimage取得や新しいCompose設定の適用を行いません。

原本文書とmodel cacheも必要な場合:

```bash
./scripts/backup.sh --stop-services --include-documents --include-caches
```

repository外へ保存するには`--output /secure/path`を指定します。

## 復元の検証と計画

restoreは既定でdry-runです。

```bash
./scripts/restore.sh --archive /secure/path/ARCHIVE.tar.gz
```

metadataを安全に展開し、path traversalとlinkを拒否し、全payload checksumを検証して、
stackを変更せず対象volumeとhost dataを表示します。

## 復元の実行

最初に計画を実行し、表示されたCompose project名を正確に指定します。

```bash
./scripts/restore.sh \
  --archive /secure/path/ARCHIVE.tar.gz \
  --execute \
  --stop-services \
  --confirm-project mrag-dify
```

上書き前に、選択archiveと同じ場所へ`pre-restore`安全backupを自動作成します。異なる
Compose project名で作られたarchiveは拒否します。

現在のhost dataを置き換える場合だけ`--restore-documents`または`--restore-secrets`を
追加します。指定しなければ`kbs_docs/`と`.env`を変更しません。

## 復元後

```bash
./scripts/verify.sh
./scripts/docling-status.sh
./scripts/sync-dry-run.sh
```

Difyから代表検索を行います。cacheを収録しなかった場合は固定modelの再取得を待ちます。
data件数と検索結果を受入するまでpre-restore backupを保持します。

## Windows

同じoptionで`win-scripts\backup.bat`と`win-scripts\restore.bat`を使用します。

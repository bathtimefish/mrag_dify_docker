# Docling取り込みコマンド

[English](./docling-ingestion-operations.md)

| Unix | Windows | 用途 |
|---|---|---|
| `./scripts/sync-dry-run.sh` | `.\win-scripts\sync-dry-run.bat` | 変換・同期計画を表示 |
| `./scripts/sync-now.sh` | `.\win-scripts\sync-now.bat` | 変換と同期 |
| `./scripts/convert-only.sh` | `.\win-scripts\convert-only.bat` | mragを変更せず変換 |
| `./scripts/reconvert-now.sh` | `.\win-scripts\reconvert-now.bat` | 全件再変換と同期 |
| `./scripts/docling-status.sh` | `.\win-scripts\docling-status.bat` | manifestと未解決errorを表示 |
| `./scripts/docling-doctor.sh` | `.\win-scripts\docling-doctor.bat` | 実効設定と接続を検証 |

変更を行う全commandが同じ同期lockを使用します。

## 診断

```bash
./scripts/docling-doctor.sh
./scripts/docling-doctor.sh --no-probe
./scripts/docling-status.sh
```

`--no-probe`はnetworkへ接続せず実効設定を表示します。statusはmanifest欠落、破損、非互換で
非zeroを返し、自動修復しません。

## 変換だけを検査

```bash
./scripts/convert-only.sh
./scripts/docling-status.sh
```

変換済みMarkdownと変換manifestを更新し、`mrag add`、`mrag remove`、`mrag index`を
実行しません。

## 強制再変換

```bash
./scripts/sync-dry-run.sh
./scripts/reconvert-now.sh
```

強制再変換は削除計画を広げません。削除は実際に存在しない原本だけから判断します。

## Staging清掃

管理commandは既定でdry-runです。

```bash
python3 batch/docling_admin.py cleanup-staging \
  --converted-dir /path/to/converted-docs \
  --older-than-seconds 86400
```

変換・同期processが動いていないことを確認後だけ`--execute`を指定します。選択した期間より
古いstaging runだけを削除します。

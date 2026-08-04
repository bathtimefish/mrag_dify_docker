# トラブルシューティング

[English](./troubleshooting.md)

最初にread-only診断を実行します。

```bash
./scripts/compose.sh ps
./scripts/compose.sh logs --tail=100 mrag-api mrag-batch docling-serve ollama
./scripts/docling-doctor.sh
./scripts/mrag-doctor.sh
./scripts/sync-dry-run.sh
```

出力を共有する前に認証情報、文書名、hostname、public IP、文書内容を削除します。

## Serviceが起動しない

```bash
./scripts/compose.sh config --quiet
./scripts/compose.sh ps -a
./scripts/compose.sh logs --tail=200 SERVICE
```

disk空き容量、port競合、Docker memory、image取得失敗、healthcheckを確認します。
修復目的で`down -v`を実行しないでください。

## Model初期化に失敗する

```bash
./scripts/compose.sh ps ollama ollama-model-init
./scripts/compose.sh logs --tail=200 ollama ollama-model-init
./scripts/compose.sh exec ollama ollama list
```

DNS、registry接続、空きstorage、期待するmodel識別子を確認します。再試行時もmodel volumeを
保持してください。

## 文書がindexされない

```bash
./scripts/docling-status.sh
./scripts/sync-dry-run.sh
./scripts/sync-now.sh
```

0 byte、走査中変更、未対応形式、変換失敗、timeout budget、削除guardを確認します。
source mountを確認するまで削除guardをoverrideしないでください。

## Difyからmragへ接続できない

次を確認します。

- Endpointが`/retrieval`を含まない`http://mrag-api:8000`
- Knowledge IDが`main_kb`
- API keyが`MRAG_API_KEY`と一致
- `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS`に`mrag-api`を設定
- `mrag-api`がhealthy

## NVIDIAが利用されない

DockerからCUDA test imageを実行できること、`.env`のaccelerator設定を確認し、install
guideのGPU検査を実行します。API応答だけではmodelがVRAMへloadされた証拠になりません。

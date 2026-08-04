# 対応環境とサイジング

[English](./supported-platforms.md)

## 対応matrix

| Host | CPU profile | NVIDIA profile | 注記 |
|---|---:|---:|---|
| Linux x86-64 | 対応 | 対応 | GPU利用にはNVIDIA Container Toolkitが必要 |
| Linux arm64 | 対応 | release試験対象外 | sqlite-vaporettoにはarm64 artifactあり |
| Windows 10/11 + Docker Desktop | 対応 | 対応 | Linux containerとWSL2 backendを使用 |
| macOS Apple Silicon / Intel | 対応 | 利用不可 | DockerからApple Metalをcontainerへ渡せない |

## Resource計画

文書量、文書の複雑さ、OCR、並列数により必要量は大きく変わります。次を開始値とし、
本番前に実際の文書集合で計測してください。

| Profile | Host CPU | Host memory | GPU | 推奨空きstorage |
|---|---:|---:|---:|---:|
| 評価用CPU | 8 core | 24 GiB | なし | 60 GiB |
| 本番CPU開始値 | 12 core以上 | 32 GiB以上 | なし | 100 GiB以上 |
| NVIDIA開始値 | 8 core以上 | 32 GiB以上 | VRAM 16 GiB以上 | 100 GiB以上 |

既定のDocling制限は8 CPU、12 GiBです。Dify、PostgreSQL、二つのQdrant、Redis、
Ollama、model cache、変換済みMarkdown、backup、原本文書には追加容量が必要です。

## Capacity確認

本番前に次を計測します。

- 初回変換と差分変換の時間
- 変換・検索同時実行時のCPU、RAM、VRAM peak
- model cache、変換成果物、index、database、backup容量
- 通常および最大文書のpage数
- 定期処理が次回開始までに完了するか

計測結果から`DOCLING_CPUS`、`DOCLING_MEMORY_LIMIT`、`DOCLING_WORKERS`、
`DOCLING_BATCH_CONCURRENCY`を設定します。memory計測なしで並列数を上げると、host負荷や
GPU out-of-memoryの原因になります。

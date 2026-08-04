# サードパーティーライセンス

[English](./THIRD_PARTY_NOTICES.md)

本リポジトリの独自コードには[MIT License](./LICENSE)が適用されます。システムが利用する
サービス、ライブラリ、コンテナイメージ、機械学習モデルには、それぞれのライセンスが
適用されます。本書は情報提供を目的とした一覧であり、正式な条件は各上流ライセンスです。

## 主要コンポーネント

| コンポーネント | 固定バージョン | ライセンス | 利用形態 |
|---|---|---|---|
| [Dify](https://github.com/langgenius/dify) | 1.16.0 / `5c6372d2f76d240265b92fd27c16bc772ffcb107` | 追加条件付きApache License 2.0 | セットアップ時にソースを取得し、公式Composeを実行 |
| [mrag](https://github.com/bathtimefish/mrag) | 1.0.0 / `84e6ca99bebfe58dcd6c358aacdcc61d7d83eb49` | MIT | Git submoduleとして取得し、独自runtime imageへ導入 |
| [Docling Serve](https://github.com/docling-project/docling-serve) | 1.28.0 | MIT | digest固定コンテナimage |
| [Ollama](https://github.com/ollama/ollama) | 0.32.3 | MIT | コンテナ化したEmbeddingサービス |
| [Qdrant](https://github.com/qdrant/qdrant) | 1.18.3 | Apache-2.0 | mrag用ベクトルデータベース |
| [sqlite-vaporetto](https://github.com/hotchpotch/sqlite-vaporetto) | 0.4.0 | MIT OR Apache-2.0 | 共有ライブラリを独自runtime imageへ収録 |

backup/restore toolはversion固定してlocal buildしたmrag runtime imageを再利用し、別の
helper imageを追加しません。

### Difyの追加条件

Difyは標準のApache License 2.0ではなく、マルチテナント運用およびフロントエンドの
ロゴ・著作権表示の削除や変更に関する追加条件を設けています。デプロイ前に、固定した
Difyソースのライセンスを確認してください。本プロジェクトのMIT LicenseはDifyの条件を
置き換えたり緩和したりするものではありません。

### sqlite-vaporettoモデル

sqlite-vaporettoの`-with-model` releaseには、BSD-3-Clauseで配布される
`bccwj-suw+unidic_pos+kana.model.zst`が含まれます。`mrag/Dockerfile`は上流の
`README.md`と`MODEL_LICENSES.md`をruntime image内の
`/usr/share/doc/sqlite-vaporetto/`へコピーします。

## 機械学習モデル

| モデル | 固定識別子 | 表示ライセンス | 利用形態 |
|---|---|---|---|
| [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) | `VERSIONS`記載のOllama `bge-m3` manifest | MIT | Embedding。初回セットアップ時に取得 |
| [hotchpotch/japanese-reranker-cross-encoder-small-v1](https://huggingface.co/hotchpotch/japanese-reranker-cross-encoder-small-v1) | `VERSIONS`記載のHugging Face revision | MIT | Reranking。初回セットアップ時に取得 |

モデル名、revision、digestを更新するたびに、成果物とライセンスを再確認してください。

## 推移的依存関係とコンテナOS

独自imageにはPython、Debianパッケージ、PyTorch、mragの推移的依存関係も含まれます。
各ライセンスファイルは通常のパッケージまたはimage内の場所に保持されます。リリース
buildではSBOMを生成し、個々のbuildの正確な収録物一覧として使用します。

DoclingのCPU imageとNVIDIA imageは別成果物です。NVIDIA imageにはNVIDIAの条件が
適用されるCUDAコンポーネントも含まれます。NVIDIA profileを選ぶ利用者は、自身の
デプロイに適用される条件を確認してください。

mragのPython依存関係の詳細は、submodule初期化後の
`vendor/mrag/THIRD_PARTY_NOTICES.md`を参照してください。

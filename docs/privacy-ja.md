# プライバシーとデータフロー

[English](./privacy.md)

本プロジェクトはhosted serviceを提供しません。運用者がhost、文書、認証情報、保持期間、
利用するmodel providerを管理します。

## Local処理

原本文書は`kbs_docs/`から同期containerへread-only mountされます。DoclingはCompose
network内で変換します。変換済みMarkdown、manifest、index、backupはlocal Docker
volumeへ保存され、OllamaはlocalでEmbeddingを生成します。

## 外部送信

初回セットアップでは固定artifactとmodelを取得するため、container registry、GitHub、
Python package index、Ollama model registry、Hugging Faceへ接続します。component更新時も
同じ上流serviceへ接続します。

Difyで外部回答生成providerを使用すると、providerへ次が送信される場合があります。

- 利用者の質問
- 検索された文書断片とmetadata
- system promptとworkflow prompt
- workflowが設定した会話履歴

機密情報をindexする前に、providerの保持・学習policyを確認してください。dataを運用者の
infrastructure内に残す必要がある場合は、localでhostしたDify対応model providerを利用できます。

## Logとsupport data

同期logには文書の相対path、hash、size、status、error詳細が含まれます。本文やAPI keyは
出力しない設計ですが、file名だけでも機密情報になり得ます。共有前にlogと画面を加工します。

## 運用者checklist

- 各文書をindexする権限があることを確認します。
- 原本、index、log、会話、backupの保持と削除要件を決めます。
- host、Docker、Dify、backupへのアクセスを制限します。
- 外部providerと、該当する場合は越境移転を文書化します。
- 文書削除が運用policyに従って原本、index、backupへ反映されることを試験します。

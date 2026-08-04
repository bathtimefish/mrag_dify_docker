# セキュリティポリシー

[English](./SECURITY.md)

## サポート対象version

最新minor releaseへセキュリティ修正を提供します。

| Version | サポート |
|---|---|
| 1.x | 対象 |
| 1.0未満 | 対象外 |

## 脆弱性の報告

脆弱性の疑いを公開Issueへ投稿しないでください。本リポジトリのGitHub Private
Vulnerability Reportingを使用してください。次の情報を含めます。

- 影響を受けるversionまたはcommit
- デプロイprofileとOS
- 再現手順と影響
- 認証情報または機密文書が露出した可能性
- 提案できる場合は修正案

ログと画面からAPI key、password、文書内容、顧客名、hostname、public IPを削除して
ください。完全な報告を受領後、深刻度を評価し、報告者と公開時期および修正を調整します。

## デプロイ責任

本リポジトリは独立して保守されるserviceを統合します。運用者はhost、Docker、Dify、
mrag、Docling、Ollama、Qdrant、取得モデルを更新し、network制限、HTTPS、backup、
秘密値を適切に管理してください。[デプロイセキュリティ](./docs/security-ja.md)も確認してください。

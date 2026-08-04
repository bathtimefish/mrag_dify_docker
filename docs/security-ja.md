# デプロイセキュリティ

[English](./security.md) · [脆弱性報告](../SECURITY-ja.md)

## 外部公開前

- 最初に信頼されたnetworkからDify管理者初期設定を完了します。
- DifyをHTTPSと信頼できるreverse proxyまたはload balancerの背後へ配置します。
- port 80/443を意図したclientだけに制限します。
- mrag、Ollama、Docling、PostgreSQL、Redis、Qdrantのportを公開しません。
- host firewallを有効にし、hostとDocker runtimeを更新します。

mragのhost portは既定で`127.0.0.1`へbindします。認証・暗号化gatewayで保護しない限り
`0.0.0.0`へ変更しないでください。

## 秘密値

`init-env.sh`は乱数値を生成し、Unixでは`.env`をmode `0600`にします。可能であれば
本番秘密値は適切なsecret managerで管理します。露出の疑いと所有権移管時にはrotateします。

`.env`、access token、API key、文書内容、model provider認証情報、完全な診断logを
Issueへ掲載しないでください。

## Containerとnetwork

- 必要なCompose profileだけを起動します。
- 内部serviceをprivate Docker networkへ維持します。
- mragとDoclingに設定したnon-root userを維持します。
- `VERSIONS`でimageとmodelを固定し、更新Pull Requestをreviewします。
- release imageとSBOMを脆弱性scanします。
- outbound通信を文書化したimage、package、model、LLM provider endpointへ制限します。

## Backup

backupにはknowledge corpus全体、Dify database、API key、管理者情報が含まれ得ます。
暗号化、read権限制限、保持期間、復元試験を実施し、未加工のbackupをIssueへ添付しません。

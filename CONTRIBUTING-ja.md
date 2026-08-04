# コントリビューション

[English](./CONTRIBUTING.md)

不具合報告、文書改善、目的を絞ったPull Requestを歓迎します。

## Issue作成前

1. 既存Issueを検索します。
2. `./scripts/verify.sh`と関連するdoctor commandを実行します。
3. ログから秘密値、文書内容、個人情報、hostname、public IPを削除します。
4. セキュリティ問題にはPrivate Vulnerability Reportingを使用します。

## 開発時の検査

```bash
python3 -m pip install -r requirements-dev.lock
pytest
ruff check batch mrag scripts tests
shellcheck scripts/*.sh batch/*.sh mrag/*.sh
```

Composeへ影響する変更では次も実行します。

```bash
./scripts/bootstrap-vendors.sh
test -f .env || ./scripts/init-env.sh
./scripts/compose.sh config --quiet
```

英語文書を既定の`*.md`へ、日本語文書を対応する`*-ja.md`へ記載し、同じPull Requestで
両方を更新してください。

## Pull Request

- 変更目的を絞り、利用者から見える動作を説明します。
- testを追加または更新します。
- 利用者向け変更では`CHANGELOG.md`と`CHANGELOG-ja.md`を更新します。
- component、image、artifact、modelを変える場合は`VERSIONS`とNoticeを更新します。
- `.env`、`kbs_docs/`、生成profile、backup、log、model fileをcommitしません。

コントリビューションには、本リポジトリと同じMIT Licenseが適用されます。

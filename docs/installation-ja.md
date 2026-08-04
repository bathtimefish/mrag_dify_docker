# インストール

[English](./installation.md) · [READMEへ戻る](../README-ja.md)

停止条件と安全な対策を含む実行可能な初回installには、
[初回セットアップRunbook](../SETUP-ja.md)を使用してください。このpageは短い参考情報です。

## 1. Profileの選択

`.env`の`OLLAMA_ACCELERATOR`と`DOCLING_ACCELERATOR`を個別に設定します。

| 値 | 利用環境 |
|---|---|
| `cpu` | 可搬な既定値、macOS、NVIDIA runtimeがないhost |
| `nvidia` | NVIDIA container runtimeが動作するLinuxまたはWindows Docker Desktop |

セットアップ前にDockerを確認します。

```bash
docker version
docker compose version
docker ps
git --version
python3 --version
```

## 2. 固定した上流sourceの取得

```bash
./scripts/bootstrap-vendors.sh
```

`VERSIONS`に記録したDify commitをcheckoutし、固定したmrag submoduleを初期化します。
どちらかのrevisionが想定外なら停止します。

## 3. ローカル設定の作成

```bash
./scripts/init-env.sh
```

乱数の認証情報を含む`.env`を作成し、Unixではmode `0600`にします。このfileをcommit、
共有しないでください。既に`.env`がある場合は保持し、不足設定だけを追加します。

```bash
./scripts/upgrade-env.sh
```

resource制限、timezone、schedule、port、acceleratorを確認します。

## 4. Buildと起動

```bash
./scripts/build.sh
./scripts/compose.sh config --quiet
./scripts/compose.sh up -d
./scripts/verify.sh
```

初回起動ではEmbeddingとrerankerモデルを取得します。回線によって時間がかかります。
再試行の間にDocker volumeを削除しないでください。

## 5. Dify初期設定

1. デプロイhostから<http://localhost/install>を開きます。
2. 信頼できないnetworkへ公開する前に管理者accountを作成します。
3. Difyが対応する任意の回答生成providerを設定します。
4. README記載の値でmragをExternal Knowledgeへ追加します。
5. Knowledge Retrievalを使うChat Workflowをimportまたは作成します。

信頼されたlocal networkの外へ公開する前にHTTPSを設定します。

## Windowsネイティブ

PowerShellまたはCommand Promptから`.bat`を使用します。

```powershell
.\win-scripts\check-prerequisites.bat
.\win-scripts\bootstrap-vendors.bat
.\win-scripts\init-env.bat
.\win-scripts\build.bat
.\win-scripts\compose.bat config --quiet
.\win-scripts\compose.bat up -d
.\win-scripts\verify.bat
```

repositoryはDocker Desktopと共有できるWindows local pathへ配置します。WSL filesystemへ
配置した場合は、WSLからUnix scriptを使用します。

## NVIDIA検証

NVIDIA profile選択前に、test用CUDA containerで`nvidia-smi`が動くことを確認します。
起動後は次を実行します。

```bash
./scripts/verify-ollama-gpu.sh
./scripts/docling-doctor.sh
```

Windowsネイティブでは`verify-ollama-gpu.bat`と`docling-doctor.bat`を使用します。

## 停止と再開

```bash
./scripts/compose.sh stop
./scripts/compose.sh up -d
```

`down`はcontainerとnetworkを削除しますがvolumeを保持します。Dify data、index、model、
backupを恒久的に削除すると明示的に決めた場合以外、`down -v`を使用しないでください。

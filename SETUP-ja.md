# 初回セットアップRunbook

[English](./SETUP.md) · [インストール参考情報](./docs/installation-ja.md) · [トラブルシューティング](./docs/troubleshooting-ja.md)

この文書は、人間とLLMエージェントの双方が使用する初回インストール手順の正本です。
順序を変更せず、上から実行してください。検証コマンドが成功を報告するまでセットアップは
完了していません。

このRunbookは安全側に設計されています。既存の設定とデータを保持し、想定外のsource
revisionでは停止し、修復手段として破壊的なDocker cleanupを使用しません。

## 安全規則

コマンドを実行する前に、次を遵守してください。

1. リポジトリルートを作業ディレクトリにします。以下のpathはすべてそこからの相対pathです。
2. コマンドが非0で終了したら停止し、続行前にエラーを確認します。
3. 既存の`.env`を上書きしません。生成済みの認証情報が含まれています。
4. セットアップや障害対応で`docker compose down -v`、`docker volume rm`、
   Docker全体のpruneを実行しません。
5. port競合の解消目的で、所有者の承認なく無関係なcontainerを停止しません。このstackの
   portを変更します。
6. 新規installでは、基盤全体の検証に成功するまで`kbs_docs/`をclone直後の状態
   （`.gitkeep`のみ）にします。既存installでは、この規則のために既存文書を削除しません。
7. `.env`、API key、文書内容、backup、未編集のlogを表示、chatへの貼り付け、commit、
   uploadしません。
8. 新規Dify環境を信頼できないnetworkへ公開しません。最初に管理者accountを作成し、
   その後HTTPSとアクセス制御を設定します。

このディレクトリに稼働中のinstallまたは重要なDocker volumeがある場合は停止してください。
この初回Runbookはupgradeやreset手順ではありません。backupを取得し、運用文書を使用します。
初回setupの中断から意図的に再開する場合だけ、ここを続行してください。
[バックアップと復元](./docs/backup-and-restore-ja.md)も参照してください。

## 1. ホスト要件の確認

必要なホストツール:

- Docker EngineまたはDocker DesktopとDocker Compose v2
- Git
- Python 3.11以降
- LinuxおよびmacOSでは`curl`
- 初回のsource、image、package、model取得に使うinternet接続

評価環境では、host CPU 8 core、memory 24 GiB、空きstorage 60 GiB程度から始めてください。
既定のDocling service limitだけで8 CPUと12 GiBを使用します。本番サイジングは文書の
複雑さ、OCR、同時実行数に依存するため、
[対応環境とサイジング](./docs/supported-platforms-ja.md)を確認してください。

### LinuxとmacOSの事前確認

```bash
docker version
docker compose version
docker ps
git --version
python3 --version
curl --version
docker compose ls
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

Dockerへ接続できない、Composeがv1、Pythonが3.11未満の場合は続行しません。port 80、
443、8000が使用中でないか確認してください。`mrag-dify`というCompose projectが既に
存在する場合は旧installの可能性があります。所有者とbackupを確認せず操作しません。

remoteまたはcloud hostで`up -d`を実行する前に、firewallまたはSecurity Groupを設定し、
port 80と443へ信頼できるセットアップ担当者だけが接続できるようにします。Dify nginxの
portはhostへ公開されるため、管理者作成画面をinternetへ開放したままにしません。

macOSではCPU profileを使用します。DockerからApple Metalをこれらのcontainerへ
渡すことはできません。

### Windowsネイティブの事前確認

Windows 10/11、WSL2 backendのDocker Desktop、Linux containerを使用します。
リポジトリは`C:\src\mrag_dify_docker`などDocker Desktopと共有できるlocal pathへ
配置してください。WSL filesystem内に置く場合は、WindowsとLinuxのscriptを混在させず、
WSL shellからLinux手順を使用します。

PowerShellまたはCommand Promptから実行します。

```powershell
.\win-scripts\check-prerequisites.bat
docker compose ls
docker ps --format "table {{.Names}}\t{{.Ports}}"
```

Dockerへ接続できない場合やWindows container modeの場合、前提条件scriptは停止します。

### NVIDIAの事前確認

NVIDIA accelerationは、NVIDIA Container Toolkitを構成したLinuxと、WSL2を使用する
Windows Docker Desktopで利用できます。NVIDIA profileを選択する前に、導入したdriverに
適合するNVIDIA Container Toolkitの検証手順を使い、CUDA container内で`nvidia-smi`が
実行できることを確認してください。

ホスト上の`nvidia-smi`が動くだけで`nvidia`を選択しません。DockerからGPUを割り当てられる
必要があります。container levelの検証が失敗した場合は両acceleratorを`cpu`のままにし、
ホストruntimeは別途修復します。

## 2. 正常なcheckoutの取得

新規にcloneする場合:

```bash
git clone --recurse-submodules https://github.com/bathtimefish/mrag_dify_docker.git
cd mrag_dify_docker
```

既にclone済みの場合はリポジトリルートへ移動し、local変更がセットアップへ影響しないことを
確認します。

```bash
git status --short
git submodule status
```

local変更を自動的に破棄しません。想定外の変更がある場合は所有者へ確認してください。

固定した上流sourceを取得して検証します。

```bash
./scripts/bootstrap-vendors.sh
```

Windowsネイティブ:

```powershell
.\win-scripts\bootstrap-vendors.bat
```

このコマンドは固定したDify checkoutとmrag submoduleを準備します。想定外のcommitを報告した
場合は停止します。検査を迂回したり`vendor/dify`を直接編集したりせず、新しいcheckoutを
使用するか、差異の原因を調査してください。

## 3. ローカル設定の作成と確認

LinuxまたはmacOSでは、次の条件分岐の一方だけが実行されます。

```bash
if [[ -f .env ]]; then
  ./scripts/upgrade-env.sh
else
  ./scripts/init-env.sh
fi
```

WindowsネイティブのPowerShell:

```powershell
if (Test-Path .env) {
    .\win-scripts\upgrade-env.bat
} else {
    .\win-scripts\init-env.bat
}
```

`init-env`はrandomな認証情報を生成し、Unixではmode `0600`も設定します。
`upgrade-env`は既存の値や秘密値を変更せず、不足する設定だけを追加します。`init-env`の
`already exists`は安全のための停止です。`.env`を削除せず`upgrade-env`を実行してください。

`.env`の内容をissueやchatへコピーせず、ローカルで確認します。CPUの既定値は全対応hostで
動作します。理解している設定だけを変更してください。

| 設定 | 安全な初期値 | 注意事項 |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `mrag-dify` | 一つのDocker hostで複数installする場合は一意にする |
| `OLLAMA_ACCELERATOR` | `cpu` | container levelのGPU検証後だけ`nvidia`にする |
| `DOCLING_ACCELERATOR` | `cpu` | Ollamaとは独立した設定 |
| `MODEL_DOWNLOAD_OFFLINE` | `false` | 新規installにはmodel downloadが必要 |
| `EXPOSE_NGINX_PORT` | `80` | 所有する別serviceがport 80を使う場合は変更する |
| `EXPOSE_NGINX_SSL_PORT` | `443` | port 443が予約済みの場合は変更する |
| `MRAG_EXPOSE_PORT` | `8000` | 既定ではloopbackだけにbindする |
| `DOCLING_CPUS` | `8.0` | hostが既定値を提供できない場合だけ下げる |
| `DOCLING_MEMORY_LIMIT` | `12g` | 小さすぎる値は変換失敗の原因になる |

portが競合する場合は、最初の`up -d`より前に`.env`で未使用の数値portを選びます。
無関係なstackを停止しません。セットアップ検証は設定済みのHTTP portとmrag portを読みます。

NVIDIAでは片方または両方のacceleratorを`nvidia`にできます。この二つは独立しています。
macOSでは両方を`cpu`のままにします。

## 4. 固定したカスタムimageのbuild

LinuxとmacOS:

```bash
./scripts/build.sh
```

Windowsネイティブ:

```powershell
.\win-scripts\build.bat
```

buildはmrag gitlink、source commit、package version、CPU PyTorchのpin、
sqlite-vaporetto checksumを検証してから、`local/mrag-runtime:1.0.0`と
`local/mrag-batch:1.0.0`を作成します。

downloadが失敗した場合はDNS、proxy、registry access、空きstorageを確認し、同じbuild
コマンドを再実行します。Dockerは完了済みlayerを再利用します。downloadを通すためにvolumeを
削除したり、固定checksumを変更したりしません。Docker Hubのrate limitが繰り返し発生する
場合はregistryへ認証するか、制限windowのresetを待ちます。

## 5. 起動前のCompose検証

LinuxとmacOS:

```bash
./scripts/compose.sh config --quiet
```

Windowsネイティブ:

```powershell
.\win-scripts\compose.bat config --quiet
```

失敗した場合は続行しません。主な原因は`.env`の欠落、Dify checkoutの未完了、不正な
accelerator値、local設定の構文エラーです。報告された入力を修正して再検証します。
生成物である`vendor/dify/docker/.env`を直接編集しません。

## 6. stack全体の起動と検証

LinuxとmacOS:

```bash
./scripts/compose.sh up -d
VERIFY_TIMEOUT_SECONDS=900 ./scripts/verify.sh
```

WindowsネイティブのPowerShell:

```powershell
.\win-scripts\compose.bat up -d
$env:VERIFY_TIMEOUT_SECONDS = "900"
.\win-scripts\verify.bat
Remove-Item Env:VERIFY_TIMEOUT_SECONDS
```

初回起動では固定したOllama Embedding modelとreranker modelを取得します。数分かかる場合が
あるため、terminalとnetworkを維持してください。再試行には同じ`up -d`と検証コマンドを
使用します。`init-env`を再実行せず、modelやdata volumeを削除しません。

検証では、常駐service、初期化service、healthcheck、DifyとmragのHTTP endpoint、
Docling認証とsmoke変換、reranker load、Redis認証、Difyからmragへの接続、Ollamaでの
Embedding生成、Qdrant access、空または実文書の同期を確認します。

検証失敗時は、最初にread-onlyの状態を収集します。

```bash
./scripts/compose.sh ps -a
./scripts/compose.sh logs --tail=200 ollama ollama-model-init mrag-init mrag-api
./scripts/compose.sh logs --tail=200 docling-serve mrag-batch api worker nginx
./scripts/docling-doctor.sh
./scripts/mrag-doctor.sh
```

Windowsネイティブでは`win-scripts\compose.bat`経由の同じコマンドと、
`docling-doctor.bat`、`mrag-doctor.bat`を使用します。

logを共有する前に、認証情報、文書名と内容、hostname、public addressを削除します。
代表的な失敗には次のように対処します。

| 症状 | 安全な対処 |
|---|---|
| serviceが`starting`のまま | memory、空きdisk、900秒で十分だったかを確認し、対象serviceのlogを見る |
| `ollama-model-init`が失敗 | 外向きDNS/HTTPSとregistry accessを確認し、`ollama_models`を保持して再試行する |
| `mrag-init`が失敗 | Ollamaとrerankerのdownload errorを確認し、mrag data、profile、model cacheを保持する |
| port割当が失敗 | `.env`でこのprojectのportを変更し、無関係なstackを停止しない |
| Docling smoke変換が失敗 | Docling log、memory pressure、API health、CPU/GPU選択を確認する |
| 同期が大量削除guardを報告 | 停止して`kbs_docs/` mountを確認し、原因不明のdata消失時は迂回しない |
| Dify endpointが読み込み中のまま | `./scripts/compose.sh restart nginx`だけを実行して再検証する |
| DockerはNVIDIAを報告するがVRAMを使わない | CPU fallbackとして扱い、次のGPU検証を完了する |

検証が失敗中、同期lockによりskip、必要modelの待機中である場合は成功を報告しません。

### NVIDIA利用の検証

`OLLAMA_ACCELERATOR=nvidia`の場合:

```bash
./scripts/verify-ollama-gpu.sh
```

Windowsネイティブ:

```powershell
.\win-scripts\verify-ollama-gpu.bat
```

このコマンドはEmbedding requestを実行し、Ollamaが0より大きいVRAM使用量を報告しなければ
失敗します。正常なHTTP responseだけではGPU実行の証明になりません。

`DOCLING_ACCELERATOR=nvidia`の場合は`docling-doctor`も実行し、Docling service logで
CUDA初期化やfallback messageを確認します。GPU割当が不明確な場合は、host runtimeを
修復するまでCPU profileを使用します。

## 7. ユーザーが所有するDify設定の完了

既定portの場合:

- Dify install: <http://localhost/install>
- mrag API仕様: <http://localhost:8000/docs>

portを変更した場合は、URLのportを`.env`の`EXPOSE_NGINX_PORT`と`MRAG_EXPOSE_PORT`へ
置き換えます。

ユーザーはDify画面で次を実行します。

1. 信頼できるhostから最初のDify管理者を作成します。
2. Difyが対応する回答生成providerを設定します。provider API keyはDifyのcredential
   storeへ入力し、セットアップエージェントへ渡しません。
3. External Knowledgeへ次を登録します。

   | 設定 | 値 |
   |---|---|
   | Endpoint | `http://mrag-api:8000` |
   | API key | local `.env`の`MRAG_API_KEY` |
   | Knowledge ID | `main_kb` |

   `/retrieval`はDifyが自動付加するため入力しません。
4. Knowledge Retrievalを含むChat Workflowをimportまたは作成します。

Difyを信頼できないnetworkから到達可能にする前に、管理者作成とHTTPS/network制御を
完了してください。

## 8. 基盤検証後の文書追加

対応fileを`kbs_docs/`へ配置し、変更をpreviewして適用します。

```bash
./scripts/sync-dry-run.sh
./scripts/sync-now.sh
```

対応入力はPDF、Markdown、plain text、Word、Excel、PowerPoint、およびREADME記載の
画像拡張子です。最初は小さな代表sampleから始めます。大量corpusを追加する前に変換状態を
確認してください。

```bash
./scripts/docling-status.sh
```

文書が失敗した場合、最後に成功した変換結果を保持してエラーを報告します。対象corpusへの
影響を理解せず、page、size、timeout、大量削除guardを無効にしません。

## 完了条件

次をすべて満たした場合だけセットアップ完了と報告できます。

- custom image buildが成功した
- Compose設定検証が成功した
- `verify.sh`または`verify.bat`が`Verification succeeded.`を表示した
- NVIDIA Ollama profileを選択した場合はGPU検証が成功した
- operatorへDifyとmragのURLを報告した
- Dify管理者作成、provider認証情報、workflow作成、HTTPS、本番access制御がユーザー作業で
  あることを明示した

以後の運用、backup、復旧、詳しい障害対応は、[運用手順](./docs/operations-ja.md)、
[バックアップと復元](./docs/backup-and-restore-ja.md)、
[トラブルシューティング](./docs/troubleshooting-ja.md)を参照してください。

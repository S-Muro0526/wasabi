# Wasabi Hot Cloud Storage ファイルダウンロードツール

## 1. 概要

本ツールは、Wasabi Hot Cloud Storage (Wasabi) からファイルをダウンロードするためのPython製コマンドラインツールです。

主な機能として、単一ファイルのダウンロード、ディレクトリ単位の一括ダウンロード、そして指定した過去の時点でのファイルバージョンの一括取得を提供します。設定は外部の`config.csv`ファイルから読み込まれ、オプションでMFA（多要素認証）にも対応しています。

## 2. 必要なライブラリ

本ツールを実行するには、以下のPythonライブラリが必要です。

- `boto3`: AWS SDK for Python (WasabiはS3互換APIを提供)
- `pandas`: 設定ファイル(`config.csv`)の読み込みに使用
- `tqdm`: ダウンロードの進捗状況をプログレスバーで表示するために使用

以下のコマンドで、必要なライブラリをすべてインストールできます。

```bash
pip install -r requirements.txt
```

## 3. セットアップ

ツールの実行前に、`config.csv`ファイルに必要な情報を設定する必要があります。

```csv
key,value
aws_access_key_id,YOUR_ACCESS_KEY
aws_secret_access_key,YOUR_SECRET_KEY
endpoint_url,https://s3.wasabisys.com
bucket_name,YOUR_BUCKET_NAME
mfa_serial_number,YOUR_MFA_SERIAL_NUMBER_ARN (optional)
```

| key | 説明 |
| :--- | :--- |
| `aws_access_key_id` | Wasabiアカウントのアクセスキーを入力します。 |
| `aws_secret_access_key` | Wasabiアカウントのシークレットアクセスキーを入力します。 |
| `endpoint_url` | WasabiのS3互換APIエンドポイントURLです。通常はこのままで問題ありません。 |
| `bucket_name` | ダウンロード対象のファイルが格納されているバケット名を入力します。 |
| `mfa_serial_number` | **【任意】** MFA認証を行う場合、IAMユーザーに紐づくMFAデバイスのARNを入力します。不要な場合は空欄のままにしてください。 |
| `ssl_verify_path` | **【任意】** プロキシ環境下などで、カスタムSSL証明書（`.pem`ファイルなど）のパスを指定します。 |

### 3.1. MFA認証について

`config.csv` ファイルで `mfa_serial_number` に有効なARNを設定すると、ツールの実行時に自動的にMFA認証が要求されます。
プログラムを実行すると、コンソールに以下のようなプロンプトが表示されます。

```bash
Enter MFA Token:
```

お使いの認証アプリケーション（例: Google Authenticator, Authy）に表示されている6桁のワンタイムパスワード（OTP）を入力し、`Enter`キーを押してください。
認証が成功すると、通常の処理が続行されます。

### 3.2. プロキシ環境とSSL証明書

企業内プロキシなどを経由して通信を行う際、SSLインスペクション（通信の復号・再暗号化）が行われることがあります。
このような環境では、`SSL validation failed` というエラーが発生する場合があります。

この問題を解決するには、プロキシが使用するカスタムSSL証明書（通常は`.pem`形式）のフルパスを`config.csv`の`ssl_verify_path`に設定してください。

**設定例:**
```csv
ssl_verify_path,C:\certs\my-proxy-ca.pem
```

## 4. 使用方法

コマンドプロンプトやターミナルから`wasabi_downloader.py`を実行します。

### 4.1. 単一ファイルのダウンロード (`download_file`)

Wasabi上の特定のファイル1つをダウンロードします。

**コマンド例:**
```bash
python wasabi_downloader.py download_file --source "path/to/remote/file.txt" --destination "C:\local\path\to\save\file.txt"
```

**引数:**
- `--source`: **[必須]** ダウンロード対象のWasabi上のオブジェクトキー（ファイルパス）。
- `--destination`: **[任意]** ローカル環境での保存先ファイルパス。指定しない場合、実行ディレクトリ配下に`Download`フォルダが作成され、その中に保存されます。

### 4.2. ディレクトリの一括ダウンロード (`download_dir`)

Wasabi上の特定のディレクトリ（プレフィックス）配下のすべてのファイルを一括でダウンロードします。

**コマンド例:**
```bash
# 特定のディレクトリをダウンロード
python wasabi_downloader.py download_dir --source "path/to/remote_dir/"

# バケット全体をダウンロード
python wasabi_downloader.py download_dir
```

**引数:**
- `--source`: **[任意]** ダウンロード対象のディレクトリパス。指定しない場合はバケット全体が対象となります。
- `--destination`: **[任意]** ローカル環境での保存先ディレクトリパス。指定しない場合、実行ディレクトリ配下に`Download`フォルダが作成され、その中に保存されます。

### 4.3. 特定時点のバージョン一括ダウンロード (`download_versioned`)

指定された日付の時点で存在していた、各ファイルの最新バージョンをすべてダウンロードします。(※対象バケットでバージョニングが有効になっている必要があります)

**コマンド例:**
```bash
# 2024年1月1日時点の状態でバケット全体をダウンロード
python wasabi_downloader.py download_versioned --timestamp "20240101"

# 特定のディレクトリを対象
python wasabi_downloader.py download_versioned --timestamp "20240101" --source "path/to/remote_dir/"
```

**引数:**
- `--timestamp`: **[必須]** 取得したい過去の時点を示す日付。フォーマットは`YYYYMMDD`。
- `--source`: **[任意]** ダウンロード対象のディレクトリパス。指定しない場合はバケット全体が対象となります。
- `--destination`: **[任意]** ローカル保存先ディレクトリパス。指定しない場合、実行ディレクトリ配下に`Download`フォルダが作成され、その中に保存されます。

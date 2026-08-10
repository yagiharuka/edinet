# EDINET 財務ビューア

金融庁EDINETの有価証券報告書をもとに、会社名・証券コード・EDINETコードから財務情報を検索し、最大4社を比較できるGitHub Pagesサイトです。

公開URL（Pages有効化後）：<https://yagiharuka.github.io/edinet/>

## できること

- 会社名、証券コード、EDINETコードによる検索
- 直近最大10期の業績・財政状態・キャッシュフロー表示
- 売上高等、営業利益、純利益、総資産などの推移グラフ
- 最大4社の比較
- 会計基準、連結・単体、対象期間、提出書類docIDの確認
- EDINET APIによる自動更新

欠損値は0へ置き換えません。金融業の経常収益など、一般事業会社の売上高と概念が異なる科目は提出時の科目名も併記します。

## 初回設定

EDINET API v2はAPIキーが必須です。またブラウザからのクロスドメイン通信を許可していないため、キーは公開HTMLではなくGitHub ActionsのSecretに保存します。

1. [EDINET API公式資料](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html)からアカウントを作成し、APIキーを発行します。
2. このリポジトリの `Settings` → `Secrets and variables` → `Actions` で、Repository secret `EDINET_API_KEY` を追加します。
3. `Actions` → `Sync EDINET data` → `Run workflow` を実行します。初回は主要72社について過去420日を走査し、最新の有価証券報告書から最大10期を抽出します。
4. GitHub Pagesが未設定の場合は、`Settings` → `Pages` → `Build and deployment` → `Source` を `GitHub Actions` にします。

以後は毎朝7:15（日本時間）と平日19:30に差分更新します。

## 構成

```text
index.html / styles.css / app.js   静的フロントエンド
scripts/edinet_sync.py             EDINET API取得・CSV抽出・JSON生成
config/company_universe.json       初回バックフィル対象企業
data/financials.json               公開する財務データ
data/company_master.json           EDINETコードリスト由来の企業名簿
.github/workflows/                 データ更新とPages公開
tests/                             APIキー不要のオフラインテスト
```

Pythonの外部パッケージは使っていません。取得したZIPは実行中だけメモリに保持し、リポジトリへ保存しません。

## データ上の注意

- 書類一覧APIは日付検索のみで、会社検索APIはありません。初回は対象企業を絞ってバックフィルし、その後は日次差分を反映します。
- 原則として連結を優先し、連結財務諸表がない会社は単体を表示します。
- IFRSには日本基準の「経常利益」に対応する標準概念がないため、経常利益は空欄になることがあります。
- 提出者独自のXBRL要素は自動抽出できない場合があります。
- このサイトは非公式で、投資助言を目的としません。正確な内容はEDINET原本を確認してください。

出典：金融庁EDINET API v2。EDINET閲覧サイトの情報をもとに本サイトが加工・作成。

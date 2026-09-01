# ニュース観察ツール（Apps Script版）

Google Sheets `yomiuri-politics-scraper` をそのままバックエンドにする構成です。

## 構成

- `シート1`: スクレイパーの全記事履歴
- `latest`: 現在表示する5記事
- `tool_logs`: 一覧提示・一覧チャット・記事選択・記事チャットの観察ログ
- `Code.gs`: Sheets読み書き + OpenAI API呼び出し + Webアプリ配信
- `Index.html`: ツールUI

## OpenAI API の設定

APIキーはコードに直接書かず、Apps Script の **スクリプト プロパティ** に保存します。

1. OpenAI PlatformでAPIキーを作成する。
2. Apps Scriptを開く。
3. 左側の **プロジェクトの設定** を開く。
4. **スクリプト プロパティ** に次を追加する。
   - プロパティ: `OPENAI_API_KEY`
   - 値: 作成したAPIキー
5. モデルを変えたい場合だけ、さらに次を追加する。
   - プロパティ: `OPENAI_MODEL`
   - 値: 例 `gpt-5.4-mini`

`OPENAI_MODEL` を設定しない場合は `gpt-5.4-mini` を使用します。

## Apps Scriptへの反映

1. `yomiuri-politics-scraper` をGoogle Sheetsで開く。
2. **拡張機能 → Apps Script** を開く。
3. 既存の `コード.gs` の中身を、このリポジトリの `apps-script/Code.gs` に置き換える。
4. `Index.html` の中身を、このリポジトリの `apps-script/Index.html` に置き換える。
5. OpenAI APIキーを上記のスクリプト プロパティに設定する。
6. **デプロイ → デプロイを管理 → 編集 → 新しいバージョン → デプロイ**。
7. WebアプリURLを開く。

## 現在の動作

1. `latest` から5記事を読み込み、セッション開始時に一度だけ表示順をランダム化する。
2. 一覧表示時に5記事すべてと表示位置を `list_presented` として記録する。
3. 一覧の下にチャット欄を表示する。
4. 一覧での第一コメントを `list_first_comment` として記録し、固定文「記録しました。次からはAIが回答します。」だけを返す。
5. 一覧での2発話目以降は、表示中の5記事すべてのタイトル・本文を文脈としてOpenAI APIに送り、回答する。ユーザー発話は `list_user_message`、AI応答は `list_ai_response` として記録する。
6. 記事はいつでも自由に開ける。記事選択時は `article_selected` を記録する。
7. 記事本文画面にも独立したチャットを表示する。
8. 記事画面の第一コメントは従来どおり `first_comment` として記録し、固定文だけを返す。
9. 記事画面の2発話目以降は、その記事本文を文脈としてOpenAI APIが必要最小限の説明を返す。ユーザー発話は `user_message`、AI応答は `ai_response` として記録する。
10. 一覧に戻ると `article_closed` を記録する。同じ記事を開き直した場合、そのセッション中の会話は保持される。

## AIの方針

一覧AIも記事AIも、先回りして大量の情報を与えないようにしています。

- 一覧AI: 5記事全部を勝手に要約しない。ユーザーが聞いたことだけ答える。特定の記事を読むよう促さない。
- 記事AI: 原文の代替となる全体要約を勝手に出さない。ユーザーが実際につまずいた箇所だけ、必要最小限に説明する。
- どちらも理解確認問題や次の記事の推薦はしない。

## 注意

GitHub上のファイルを更新しても、すでに作成済みのApps Script Webアプリには自動反映されません。`Code.gs` と `Index.html` をApps Script側へ貼り直し、デプロイを更新してください。

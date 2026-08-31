# ニュース読解観察ツール（Apps Script版）

Google Sheets `yomiuri-politics-scraper` をそのままバックエンドにする最小構成です。

## 構成

- `シート1`: スクレイパーの全記事履歴
- `latest`: 現在表示する5記事
- `tool_logs`: 一覧提示・記事選択・コメントの観察ログ
- `Code.gs`: Sheets読み書き + Webアプリ配信
- `Index.html`: ツールUI

## Apps Scriptへの反映

1. `yomiuri-politics-scraper` をGoogle Sheetsで開く。
2. **拡張機能 → Apps Script** を開く。
3. 既存の `コード.gs` の中身を、このリポジトリの `apps-script/Code.gs` に置き換える。
4. Apps Scriptで **＋ → HTML** を選び、ファイル名を `Index` にする。
5. `Index.html` の中身を、このリポジトリの `apps-script/Index.html` に置き換える。
6. **デプロイ → 新しいデプロイ → ウェブアプリ**。
7. 「次のユーザーとして実行」は **自分**、「アクセスできるユーザー」は観察参加者が開ける範囲を選ぶ。
8. デプロイ後のURLを開く。

## 現在の動作

1. `latest` から5記事を一覧表示。
2. 一覧表示時に、その5記事すべてを `tool_logs` に `list_presented` として記録。
3. 記事選択時に `article_selected` を記録。
4. 記事本文を表示。
5. 第一コメントを `first_comment` として記録。
6. 第一コメント送信後、固定文「ここまでのコメントは記録しました。次からはAIが回答します。」を表示。
7. 2発話目以降は `user_message` として記録するが、現段階ではAI APIを呼ばない。
8. 一覧へ戻る時に `article_closed` を記録。

AI API利用許可後は、2発話目以降の送信処理にAPI呼び出しを追加する。

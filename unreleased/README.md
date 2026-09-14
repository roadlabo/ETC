# 開発・未公開ファイル

- `legacy/`: 旧版・未公開スクリプト。現行版の起動には不要です。
- `tests/`: 回帰テストとテスト用素材。
- `docs/`: 設計メモ、記事原稿、旧版の説明。
- `samples/`: 特定地域のサンプル。
- `build_release.py`: 配布一式を作成する開発用スクリプト。
- `verify_release.py`: 配布コピーの全ファイルと移動したtilesのハッシュを検証します。
- `smoke_release.py`: 配布側ランタイムでランチャーとローカル地図の表示を確認します。

リポジトリ直下で `runtime\python\python.exe unreleased\build_release.py` を実行すると、`release/ETC_日時` に独立した配布一式を作ります。既存の配布フォルダには上書きしません。tiles・Pythonランタイムを含み、ログ・個人設定・未公開コード・テスト・Git情報は含みません。

`release/ETC` は今回作成した配布版です。中身をまとめてコピーしてください。再作成時は新しく生成された日時付きフォルダを使用してください。

テストは `runtime\python\python.exe -X utf8 unreleased\run_tests.py` で実行できます。Qt初期化の順序をそろえ、個人のプロジェクト設定を変更せずに検証します。

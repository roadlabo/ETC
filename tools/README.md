# 開発・配布用ツール

プロジェクト直下で実行します。

- テスト: `runtime\python\python.exe -X utf8 tools\run_tests.py`
- 配布作成: `runtime\python\python.exe tools\build_release.py`
- 内容照合: `runtime\python\python.exe tools\verify_release.py release\ETC_日時`
- 起動確認: `release\ETC_日時\runtime\python\python.exe -B -X utf8 tools\smoke_release.py release\ETC_日時`

配布作成は`release/ETC_日時`を新規作成し、既存の配布版には上書きしません。配布対象は`build_release.py`の`CONTENTS`と`ignore_release_files`で管理します。`docs/development`、テスト、開発ツール、サンプル、個人設定、ログは配布しません。

現行プログラムの改修は`src/`と`bat/`で行います。旧版はGit履歴にあり、必要な回帰テストは固定コミットから読み込みます。バックアップ用の旧版フォルダは作りません。

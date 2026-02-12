# USB配布向け: embeddable Python + 同梱依存（05/33 GUI維持）

この手順は、**管理PC（pip実行可能）で一度だけ**実施します。  
目的は、`runtime\python`（embeddable Python）で `05_route_mapper_simple.py` / `33_branch_check.py` を **PyQt GUIのまま** 起動できるようにし、配布先PCでは pip 不要にすることです。

## 1) 管理PCで依存を同梱する

リポジトリ直下で実行:

```bat
mkdir runtime\pydeps
runtime\python\python.exe -m pip install --upgrade pip
runtime\python\python.exe -m pip install --target runtime\pydeps -r requirements.txt
```

必要に応じて個別追加:

```bat
runtime\python\python.exe -m pip install --target runtime\pydeps PyQt6 PyQt6-WebEngine numpy pandas folium matplotlib tqdm
```

> `runtime\pydeps` 配下に `.pyd` / `.dll` / パッケージ群が展開されます。

## 2) USBへ配布する

以下を**そのまま相対パス構成で**コピー:

- `runtime\python\...`
- `runtime\pydeps\...`
- `src\...`
- `bat\run_05_lite.bat`
- `bat\run_33_lite.bat`
- `src\leaflet\...`

## 3) 配布先PCでの使い方（pip不要）

- `bat\run_05_lite.bat` をダブルクリック（GUI起動）
- `bat\run_33_lite.bat` をダブルクリック（GUI起動）
- ドラッグ&ドロップ対応:
  - CSV を `run_33_lite.bat` にドロップ → `--csv` 指定で直接起動
  - フォルダを `run_05_lite.bat` にドロップ → 第1引数として起動

## 4) トラブル時

- `ImportError` が出る場合は、管理PCで不足パッケージを `runtime\pydeps` へ `--target` 追加し、再配布します。
- `--nogui` 実行時でも、CSV/フォルダ未指定なら PyQt6 ダイアログを優先し、不可時は保険動作へフォールバックします。

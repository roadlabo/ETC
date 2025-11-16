"""Rename 2nd screening CSV files containing "__ID" in their names.

This utility scans CSV files under ``TARGET_ROOT`` and replaces the
"__ID" portion of their base names with "_ID", avoiding collisions.
Adjust the configuration section below before running.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

# リネーム対象のルートフォルダ（第2スクリーニングの出力一式が入っているフォルダ）
TARGET_ROOT = Path(r"C:\path\to\2nd_screening_output")

# サブフォルダも再帰的に処理するかどうか
RECURSIVE = True


def log(msg: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}")


def iter_csv_files(root: Path, recursive: bool):
    if recursive:
        yield from (p for p in root.rglob("*.csv") if p.is_file())
    else:
        yield from (p for p in root.glob("*.csv") if p.is_file())


def main() -> int:
    if not TARGET_ROOT.exists() or not TARGET_ROOT.is_dir():
        print(f"TARGET_ROOT が存在しないかフォルダではありません: {TARGET_ROOT}")
        return 1

    start = datetime.now()
    log(f"第2スクリーニング出力のファイル名修正を開始します。root={TARGET_ROOT}")

    scanned = 0
    affected = 0
    renamed = 0
    skipped_conflict = 0

    for path in iter_csv_files(TARGET_ROOT, RECURSIVE):
        scanned += 1
        old_name = path.name
        if "__ID" not in old_name:
            continue
        affected += 1
        new_name = old_name.replace("__ID", "_ID")
        new_path = path.with_name(new_name)

        if new_path.exists():
            log(f"[WARN] リネーム先が既に存在するためスキップ: {path} -> {new_path}")
            skipped_conflict += 1
            continue

        path.rename(new_path)
        renamed += 1

    end = datetime.now()
    log("処理が完了しました。")
    log(f"スキャンしたCSVファイル数   : {scanned}")
    log(f"\"__ID\" を含んでいたファイル: {affected}")
    log(f"リネーム実行件数            : {renamed}")
    log(f"衝突によりスキップした件数  : {skipped_conflict}")
    log(f"開始: {start}, 終了: {end}, 経過: {end - start}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

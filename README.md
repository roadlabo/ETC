# ETC Tools

ETC2.0 解析用のスクリプト集です。

---

## 1️⃣ ZIP → 運行IDごとに抽出・整列

**スクリプト**：`src/split_by_opid_streaming.py`

- ZIP内の `data.csv` を運行ID（4列目）ごとに抽出し、  
  7列目（GPS時刻）で昇順に並べたCSVを出力します。
- 入力・出力フォルダなどはスクリプト先頭で設定。
- 実行方法：

```bash
python src/split_by_opid_streaming.py

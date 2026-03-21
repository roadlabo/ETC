# ETC Analyzer 配布フロー完全版（バッチ配置明示版）

## 1. 全体構成（最重要）
```text
D:\GitHub\ETC\_EtcAnalyzer        ← 開発用（触る）
    └─src

D:\_EtcAnalyzer_work              ← 作業用（毎回使い捨て）
    ├─src         ← コピーされた開発ソース
    ├─src_obf     ← 難読化後（完成）
    ├─logs
    ├─temp
    └─tools       ← ★バッチはここに置く

D:\_EtcAnalyzer_release           ← 配布テンプレ（固定）
    ├─src
    ├─bat
    └─runtime
```

---

## 2. バッチファイルの配置（超重要）

### ■ 保存場所（ここに統一）
```
D:\_EtcAnalyzer_work\tools
```

### ■ 配置するバッチ
```
tools\obfuscate_src.bat
```

## 4. 実行の流れ（バッチ基準）

### ① srcコピー（手 or バッチ）
```
GitHub → work\src
```

---

### ② 難読化（ここでバッチ）
```
D:\_EtcAnalyzer_work\tools\obfuscate_src.bat
```

👉 実行場所はどこでもOK  
👉 中でパス指定しているため

---

### ③ 配布反映（手 or build_release.bat）
```
src_obf → release\src
```

---

## 6. 最終イメージ

```text
work/
├─tools  ← ★ここが司令塔
│   ├─obfuscate_src.bat
│   └─build_release.bat
├─src
├─src_obf
└─logs
```

👉 tools ＝「ビルド司令室」

---

## 7. 結論

✔ バッチはすべて tools に集約  
✔ src / release とは完全分離  
✔ 作業は tools から実行  

👉 これで迷いゼロ

---

以上を正式運用とする

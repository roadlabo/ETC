# docs/30_crossroad.md — 交差点解析（31_crossroad_trip_performance.py）

本章は 31_crossroad_trip_performance.py の設計思想を示す。

---

## 基本的な考え方

- A方向 / B方向は幾何学的に定義
- 流入・流出は交差点中心点基準
- 滞留は「低速通過Trip」の代理指標

---

## 注意点

- 信号現示は取得していない
- 渋滞長を直接算出するものではない
- 相対比較向きの指標である

# docs/01_pipeline.md — 処理パイプライン概要

本章では src/ 内のスクリプトを番号順に整理する。

---

## 01系：前処理

### 01_split_by_opid_streaming.py
OPID単位で巨大CSVを分割する前処理スクリプト。

---

## 05–06系：経路マッピング

### 05_route_mapper_simple.py
簡易的な経路マッピング。

### 06_route_mapper_kp.py
キーポイントを用いた経路マッピング。

---

## 10系：空間定義・サンプリング

### 10_route_sampler.py
経路上の代表点を抽出する。

### 11_crossroad_sampler.py
交差点中心点とA/B方向ベクトルを定義する。

### 12_polygon_builder.html
分析対象エリアを手動で定義するHTMLツール。

---

## 20系：Trip抽出

### 20_route_trip_extractor.py
指定経路を通過したTripを抽出する。

### 21_point_trip_extractor.py
任意地点を通過したTripを抽出する。

---

## 30系：性能評価・可視化

### 30_build_performance.py
Tripデータから基本性能指標を構築する。

### 31_crossroad_trip_performance.py
交差点単位の流入・流出・滞留指標を算出する。

### 32_crossroad_viewer.py
交差点性能の可視化を行う。

---

## 40系：OD分析

### 40_trip_od_screening.py
OD抽出前のTripスクリーニング。

### 42_OD_extractor.py
Tripデータから起終点（OD）を抽出する。

### 41_od_heatmap_viewer.py
OD分布をヒートマップで可視化する。

---

## 70系：Path分析

### 71_Path_Analysis.py
Path分類および流入・流出方向の解析。

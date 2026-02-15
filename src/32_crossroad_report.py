import argparse
import os
import re
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
try:
    from openpyxl.drawing.image import Image as XLImage
except Exception as _exc:
    XLImage = None
    print(f"[WARN] openpyxl image feature disabled (Pillow missing?): {_exc}")
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.worksheet.page import PageMargins

print(
    "[BOOT] 32 start "
    f"py={sys.executable} "
    f"ver={sys.version.split()[0]} "
    f"cwd={os.getcwd()} "
    f"argv={' '.join(sys.argv)}",
    flush=True,
)

BATCH_JOBS: list[dict] = []
BATCH_MODE_ACTIVE = False
FOLDER_CROSS = "11_交差点(Point)データ"
FOLDER_31OUT = "31_交差点パフォーマンス"
FOLDER_32OUT = "32_交差点レポート"

COL_FILE = "抽出CSVファイル名"
COL_DATE = "運行日"
COL_VTYPE = "自動車の種別"
COL_USE = "用途"
COL_IN_BRANCH = "流入枝番"
COL_OUT_BRANCH = "流出枝番"
COL_DIST = "計測距離(m)"
COL_TIME = "所要時間(s)"
COL_T0 = "閑散時所要時間(s)"
COL_DELAY = "遅れ時間(s)"
COL_TIME_VALID = "所要時間算出可否"
COL_TIME_REASON = "所要時間算出不可理由"
COL_TIME_PRIMARY = "計測開始_GPS時刻(補間)"
COL_TIME_FALLBACK = "算出中心_GPS時刻"

DELAY_BINS = [
    (0, 5),
    (5, 10),
    (10, 20),
    (20, 30),
    (30, 60),
    (60, 120),
    (120, 180),
    (180, None),
]
DELAY_LABELS = ["0-5", "5-10", "10-20", "20-30", "30-60", "60-120", "120-180", "180+"]
TIME_LABELS = ["1-4時", "4-7時", "7-10時", "10-13時", "13-16時", "16-19時", "19-22時", "22-1時"]
MAP_SCALE = 0.26
MAP_ANCHOR_CELL = "B11"


def parse_center_datetime(val) -> datetime | None:
    if val is None:
        return None
    if pd.isna(val):
        return None
    text = str(val).strip()
    if not text:
        return None

    patterns = [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y%m%d%H%M%S",
        "%H:%M:%S",
        "%H:%M",
    ]

    for fmt in patterns:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def hour_to_time_bin(hour: int) -> int:
    if hour in (22, 23, 0):
        return 7
    idx = (hour - 1) // 3
    return max(0, min(idx, 6))


def parse_operation_date(value: str):
    text = str(value).strip()
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None


class _ExcelReportHelper:
    def __init__(
        self,
        crossroad_path: Path,
        image_path: Path,
        performance_path: Path,
        clean_df: pd.DataFrame,
        unique_dates: list[date],
    ) -> None:
        self.crossroad_path = crossroad_path
        self.image_path = image_path
        self.performance_path = performance_path
        self.clean_df = clean_df
        self.unique_dates = unique_dates

    def create(self, output_xlsx: Path) -> None:
        self._create_excel_report(output_xlsx)

    def _create_excel_report(self, save_path: Path) -> None:
        wb = Workbook()
        ws_report = wb.active
        ws_report.title = "Report"
        ws_delay = wb.create_sheet("遅れ時間（データ）")
        ws_time = wb.create_sheet("時間帯（データ）")

        self._configure_report_sheet(ws_report)
        combos = self._collect_combination_data()
        self._populate_delay_data_sheet(ws_delay, combos)
        self._populate_time_data_sheet(ws_time, combos)
        self._populate_report_sheet(ws_report, combos)

        wb.save(save_path)

    def _configure_report_sheet(self, ws) -> None:
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins = PageMargins(
            left=0.7874,
            right=0.5906,
            top=0.7480,
            bottom=0.5512,
            header=ws.page_margins.header,
            footer=ws.page_margins.footer,
        )
        ws.print_options.horizontalCentered = True
        ws.print_title_rows = "1:1"
        ws.column_dimensions["A"].width = 14.29
        ws.column_dimensions["B"].width = 11.86
        ws.column_dimensions["C"].width = 9.5
        for col in ["D", "E", "F", "G", "H", "I", "J", "K"]:
            ws.column_dimensions[col].width = 7.0

    def _collect_combination_data(self) -> list[dict]:
        total_days = len(self.unique_dates)
        combos: list[dict] = []
        grouped = self.clean_df.groupby(["in_b", "out_b"])
        for (in_b, out_b), subset in grouped:
            count_total = len(subset)
            daily_count = count_total / total_days if total_days else 0
            ok_subset = subset[subset["time_valid"] == 1]
            avg_delay = ok_subset["delay_s"].mean() if not ok_subset.empty else 0
            total_delay = ok_subset["delay_s"].sum() if not ok_subset.empty else 0
            daily_total_delay_s = total_delay / total_days if total_days else 0
            daily_total_delay_min = daily_total_delay_s / 60 if total_days else 0
            delay_per_day = self._calc_delay_per_day_counts(ok_subset["delay_s"], total_days)
            time_per_day, time_parse_ng_count, time_bin_total = self._calc_time_per_day_counts(
                subset["time"], total_days
            )
            ok_count = len(ok_subset)
            ok_per_day = ok_count / total_days if total_days else 0
            time_bins_total_per_day = sum(time_per_day)
            delay_bins_total_per_day = sum(delay_per_day)
            print(
                "[CHECK] direction="
                f"{int(in_b)}→{int(out_b)} "
                f"daily_count={daily_count:.6f} "
                f"sum_time_bins_per_day={time_bins_total_per_day:.6f} "
                f"ok_per_day={ok_per_day:.6f} "
                f"sum_delay_bins_per_day={delay_bins_total_per_day:.6f} "
                f"daily_total_delay_s={daily_total_delay_s:.6f} "
                f"daily_total_delay_min={daily_total_delay_min:.6f} "
                f"avg_delay={avg_delay:.6f} "
                f"time_parse_ng_count={time_parse_ng_count} "
                f"time_bin_total={time_bin_total}"
            )

            combos.append(
                {
                    "in_b": int(in_b),
                    "out_b": int(out_b),
                    "count_total": count_total,
                    "daily_count": daily_count,
                    "avg_delay": avg_delay,
                    "total_delay": total_delay,
                    "daily_total_delay": daily_total_delay_s,
                    "delay_per_day": delay_per_day,
                    "time_per_day": time_per_day,
                }
            )

        combos.sort(key=lambda x: (-x["count_total"], x["in_b"], x["out_b"]))
        return combos

    def _calc_delay_per_day_counts(self, delay_series: pd.Series, total_days: int) -> list[float]:
        delays = pd.to_numeric(delay_series, errors="coerce").dropna().astype(float).tolist()
        counts = [0 for _ in DELAY_BINS]
        for v in delays:
            for idx, (low, high) in enumerate(DELAY_BINS):
                if high is None:
                    if v >= low:
                        counts[idx] += 1
                        break
                elif low <= v < high:
                    counts[idx] += 1
                    break
        if total_days == 0:
            return [0.0 for _ in DELAY_BINS]
        return [c / total_days for c in counts]

    def _calc_time_per_day_counts(self, time_series: pd.Series, total_days: int) -> tuple[list[float], int, int]:
        counts = [0 for _ in TIME_LABELS]
        time_parse_ng_count = 0
        for value in time_series.tolist():
            dt = parse_center_datetime(value)
            if dt is None:
                time_parse_ng_count += 1
                hour = 0
            else:
                hour = dt.hour
            bin_idx = hour_to_time_bin(hour)
            counts[bin_idx] += 1
        if total_days == 0:
            return [0.0 for _ in TIME_LABELS], time_parse_ng_count, sum(counts)
        return [c / total_days for c in counts], time_parse_ng_count, sum(counts)

    def _populate_delay_data_sheet(self, ws, combos: list[dict]) -> None:
        headers = [
            "方向（流入→流出）",
            "総台数（台）",
            "日あたり台数（台/日）",
            "平均遅れ時間（秒）",
            "1日あたり総遅れ時間（秒/日）",
            "階級（秒）",
            "台数（台/日）",
        ]
        ws.append(headers)
        for combo in combos:
            direction = f"{combo['in_b']}→{combo['out_b']}"
            base_info = [
                direction,
                combo["count_total"],
                combo["daily_count"],
                combo["avg_delay"],
                combo["daily_total_delay"],
            ]
            for label, per_day in zip(DELAY_LABELS, combo["delay_per_day"]):
                ws.append(base_info + [label, per_day])

        for row in ws.iter_rows(min_row=2, min_col=3, max_col=5):
            for cell in row:
                cell.number_format = "0.0"
        for row in ws.iter_rows(min_row=2, min_col=7, max_col=7):
            for cell in row:
                cell.number_format = "0.0"

    def _populate_time_data_sheet(self, ws, combos: list[dict]) -> None:
        headers = [
            "方向（流入→流出）",
            "総台数（台）",
            "日あたり台数（台/日）",
            "平均遅れ時間（秒）",
            "1日あたり総遅れ時間（秒/日）",
            "階級（時）",
            "台数（台/日）",
        ]
        ws.append(headers)
        for combo in combos:
            direction = f"{combo['in_b']}→{combo['out_b']}"
            base_info = [
                direction,
                combo["count_total"],
                combo["daily_count"],
                combo["avg_delay"],
                combo["daily_total_delay"],
            ]
            for label, per_day in zip(TIME_LABELS, combo["time_per_day"]):
                ws.append(base_info + [label, per_day])

        for row in ws.iter_rows(min_row=2, min_col=3, max_col=5):
            for cell in row:
                cell.number_format = "0.0"
        for row in ws.iter_rows(min_row=2, min_col=7, max_col=7):
            for cell in row:
                cell.number_format = "0.0"

    def _populate_report_sheet(self, ws, combos: list[dict]) -> None:
        title_cell = ws.cell(row=1, column=1, value="ETC2.0 交差点パフォーマンス調査")
        title_cell.font = Font(size=16, bold=True)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=11)
        title_cell.alignment = Alignment(horizontal="center")

        summary_start_row = 3
        self._write_summary_block(ws, summary_start_row)
        image_obj = self._create_resized_image()
        if image_obj:
            ws.add_image(image_obj, MAP_ANCHOR_CELL)

        combos_for_report = [c for c in combos if int(c["in_b"]) != int(c["out_b"])]
        combos_for_report.sort(key=lambda x: (-x["count_total"], int(x["in_b"]), int(x["out_b"])))

        time_title_row = 27
        time_header_row = time_title_row + 1
        time_data_row = time_title_row + 2
        time_last_row = self._write_time_table_pdf_style(
            ws, combos_for_report, time_title_row, time_header_row, time_data_row
        )

        delay_title_row = time_last_row + 2
        delay_header_row = delay_title_row + 1
        delay_data_row = delay_title_row + 2
        self._write_delay_table_pdf_style(
            ws, combos_for_report, delay_title_row, delay_header_row, delay_data_row
        )

    def _write_summary_block(self, ws, start_row: int) -> int:
        start_date, end_date = (None, None)
        if self.unique_dates:
            start_date = self.unique_dates[0]
            end_date = self.unique_dates[-1]

        weekday_map = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金", 5: "土", 6: "日"}
        weekday_order = [0, 1, 2, 3, 4, 5, 6]
        weekdays = ""
        if self.unique_dates:
            unique_weekdays = sorted({d.weekday() for d in self.unique_dates}, key=weekday_order.index)
            weekdays = "・".join(weekday_map[d] for d in unique_weekdays)

        total_records = len(self.clean_df)
        ok_records = int(self.clean_df["time_valid"].sum()) if not self.clean_df.empty else 0
        ng_records = total_records - ok_records
        info_pairs = [
            ("交差点定義ファイル", self.crossroad_path.name, None),
            ("パフォーマンスCSV", self.performance_path.name, None),
            (
                "総日数",
                f"{len(self.unique_dates)}日 {self._format_date_range(start_date, end_date)}".strip(),
                None,
            ),
            ("対象曜日", weekdays, None),
            ("総レコード数（通過）（台）", total_records, None),
            ("所要時間算出 OK/NG（台）", f"{ok_records} / {ng_records}", None),
        ]

        for offset, (label, value, extra) in enumerate(info_pairs):
            row_idx = start_row + offset
            label_cell = ws.cell(row=row_idx, column=1, value=f"{label}:")
            label_cell.font = Font(bold=True)
            label_cell.alignment = Alignment(wrap_text=False)
            value_cell = ws.cell(row=row_idx, column=4, value=value)
            value_cell.alignment = Alignment(wrap_text=False)

        return start_row + len(info_pairs)

    @staticmethod
    def _format_date_range(start_date: date | None, end_date: date | None) -> str:
        if not start_date or not end_date:
            return ""
        if start_date == end_date:
            return start_date.strftime("%Y-%m-%d")
        return f"{start_date.strftime('%Y-%m-%d')}～{end_date.strftime('%Y-%m-%d')}"

    def _write_time_table_pdf_style(
        self, ws, combos: list[dict], title_row: int, header_row: int, data_row: int
    ) -> int:
        max_col = 11
        ws.cell(row=title_row, column=1, value="")
        ws.cell(row=title_row, column=2, value="")
        ws.cell(row=title_row, column=3, value="")
        ws.merge_cells(start_row=title_row, start_column=4, end_row=title_row, end_column=max_col)
        title_cell = ws.cell(row=title_row, column=4, value="時間帯ヒストグラム（台/日）")
        title_cell.font = Font(bold=True)
        title_cell.alignment = Alignment(horizontal="center", vertical="center")

        headers = [
            "方向\n（流入→流出）",
            "日あたり\n台数\n（台/日）",
            "24h/\n7-19時\n（昼夜率）",
            "1-4\n時",
            "4-7\n時",
            "7-10\n時",
            "10-13\n時",
            "13-16\n時",
            "16-19\n時",
            "19-22\n時",
            "22-1\n時",
        ]
        for col, text in enumerate(headers, start=1):
            cell = ws.cell(row=header_row, column=col, value=text)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[header_row].height = 50

        row_idx = data_row
        for combo in combos:
            direction = f"{combo['in_b']}→{combo['out_b']}"
            daytime_total = sum(combo["time_per_day"][2:6])
            day_night_ratio = (
                combo["daily_count"] / daytime_total if daytime_total else None
            )
            values = [
                direction,
                round(combo["daily_count"], 1),
                round(day_night_ratio, 2) if day_night_ratio is not None else None,
                *[round(v, 1) for v in combo["time_per_day"]],
            ]
            for col, val in enumerate(values, start=1):
                cell = ws.cell(row=row_idx, column=col, value=val)
                cell.alignment = Alignment(horizontal="center" if col == 1 else "right", vertical="center")
                if col == 3:
                    cell.number_format = "0.00"
                if col == 2 or col >= 4:
                    cell.number_format = "0.0"
            row_idx += 1

        total_row = row_idx
        total_daily = sum(combo["daily_count"] for combo in combos)
        total_time_bins = [
            sum(combo["time_per_day"][idx] for combo in combos) for idx in range(len(TIME_LABELS))
        ]
        total_daytime = sum(total_time_bins[2:6])
        total_ratio = total_daily / total_daytime if total_daytime else None
        total_values = [
            "合計",
            round(total_daily, 1),
            round(total_ratio, 2) if total_ratio is not None else None,
            *[round(v, 1) for v in total_time_bins],
        ]
        for col, val in enumerate(total_values, start=1):
            cell = ws.cell(row=total_row, column=col, value=val)
            cell.alignment = Alignment(horizontal="center" if col == 1 else "right", vertical="center")
            if col == 3:
                cell.number_format = "0.00"
            if col == 2 or col >= 4:
                cell.number_format = "0.0"
        ws.row_dimensions[total_row].height = 18

        self.apply_table_borders(ws, title_row, 1, total_row, max_col)
        self._apply_row_bottom_border(ws, header_row, 1, max_col)
        self._apply_row_bottom_border(ws, total_row, 1, max_col)
        return total_row

    def _write_delay_table_pdf_style(
        self, ws, combos: list[dict], title_row: int, header_row: int, data_row: int
    ) -> int:
        max_col = 11
        ws.cell(row=title_row, column=1, value="")
        ws.cell(row=title_row, column=2, value="")
        ws.cell(row=title_row, column=3, value="")
        ws.merge_cells(start_row=title_row, start_column=4, end_row=title_row, end_column=max_col)
        title_cell = ws.cell(row=title_row, column=4, value="遅れ時間ヒストグラム（台/日）")
        title_cell.font = Font(bold=True)
        title_cell.alignment = Alignment(horizontal="center", vertical="center")

        headers = [
            "方向\n（流入→流出）",
            "日あたり\n総遅れ時間\n（分・台/日）",
            "平均\n遅れ時間\n（秒）",
            "0-5秒",
            "5-10\n秒",
            "10-20\n秒",
            "20-30\n秒",
            "30-60\n秒",
            "60-\n120秒",
            "120～\n180秒",
            "180秒\n～",
        ]
        for col, text in enumerate(headers, start=1):
            cell = ws.cell(row=header_row, column=col, value=text)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[header_row].height = 45

        row_idx = data_row
        for combo in combos:
            direction = f"{combo['in_b']}→{combo['out_b']}"
            values = [
                direction,
                round(combo["daily_total_delay"] / 60.0, 1),
                round(combo["avg_delay"], 1),
                *[round(v, 1) for v in combo["delay_per_day"]],
            ]
            for col, val in enumerate(values, start=1):
                cell = ws.cell(row=row_idx, column=col, value=val)
                cell.alignment = Alignment(horizontal="center" if col == 1 else "right", vertical="center")
                if col >= 2:
                    cell.number_format = "0.0"
            row_idx += 1

        last_row = row_idx - 1
        self.apply_table_borders(ws, title_row, 1, last_row, max_col)
        self._apply_row_bottom_border(ws, header_row, 1, max_col)
        return row_idx - 1

    @staticmethod
    def apply_table_borders(ws, min_row: int, min_col: int, max_row: int, max_col: int) -> None:
        thin = Side(style="thin")
        medium = Side(style="medium")
        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                left = medium if col == min_col else thin
                right = medium if col == max_col else thin
                top = medium if row == min_row else thin
                bottom = medium if row == max_row else thin
                ws.cell(row=row, column=col).border = Border(
                    left=left, right=right, top=top, bottom=bottom
                )

    @staticmethod
    def _apply_row_bottom_border(ws, row: int, min_col: int, max_col: int) -> None:
        medium = Side(style="medium")
        for col in range(min_col, max_col + 1):
            cell = ws.cell(row=row, column=col)
            existing = cell.border
            cell.border = Border(
                left=existing.left,
                right=existing.right,
                top=existing.top,
                bottom=medium,
            )

    def _create_resized_image(self) -> "XLImage | None":
        if XLImage is None:
            return None
        if not self.image_path.exists():
            return None
        image = XLImage(str(self.image_path))
        try:
            original_width = image.width
            original_height = image.height
        except Exception:
            return image

        if original_width and original_height:
            image.width = max(1, int(original_width * MAP_SCALE))
            image.height = max(1, int(original_height * MAP_SCALE))
        elif image.width and image.height:
            image.width = max(1, int(image.width * MAP_SCALE))
            image.height = max(1, int(image.height * MAP_SCALE))
        return image

def create_excel_report_headless(
    crossroad_csv: Path,
    crossroad_img: Path,
    performance_csv: Path,
    output_xlsx: Path,
) -> None:
    df_perf = pd.read_csv(performance_csv, encoding="shift_jis")

    encodings = ["shift_jis", "cp932", "utf-8"]
    df_cross = None
    for enc in encodings:
        try:
            df_cross = pd.read_csv(crossroad_csv, encoding=enc)
            break
        except Exception:
            continue
    if df_cross is None:
        raise RuntimeError("交差点定義ファイルの読み込みに失敗しました。")

    required_cols = [
        COL_FILE,
        COL_DATE,
        COL_VTYPE,
        COL_USE,
        COL_IN_BRANCH,
        COL_OUT_BRANCH,
        COL_DIST,
        COL_TIME,
        COL_T0,
        COL_DELAY,
        COL_TIME_VALID,
        COL_TIME_REASON,
        COL_TIME_PRIMARY,
        COL_TIME_FALLBACK,
    ]
    missing = [c for c in required_cols if c not in df_perf.columns]
    if missing:
        raise RuntimeError(f"必要な列が見つかりません: {', '.join(missing)}")

    date_series = df_perf[COL_DATE].astype(str).apply(parse_operation_date)
    in_branch = pd.to_numeric(df_perf[COL_IN_BRANCH], errors="coerce")
    out_branch = pd.to_numeric(df_perf[COL_OUT_BRANCH], errors="coerce")
    time_val = pd.to_numeric(df_perf[COL_TIME], errors="coerce")
    t0_val = pd.to_numeric(df_perf[COL_T0], errors="coerce")
    delay_val = pd.to_numeric(df_perf[COL_DELAY], errors="coerce")
    time_valid = pd.to_numeric(df_perf[COL_TIME_VALID], errors="coerce")

    t_primary = df_perf[COL_TIME_FALLBACK].fillna("").astype(str).str.strip()
    t_fallback = df_perf[COL_TIME_PRIMARY].fillna("").astype(str).str.strip()
    time_series = t_primary.where(t_primary != "", t_fallback)

    data = pd.DataFrame(
        {
            "date": date_series,
            "in_b": in_branch,
            "out_b": out_branch,
            "time_s": time_val,
            "t0_s": t0_val,
            "delay_s": delay_val,
            "time_valid": time_valid,
            "time": time_series,
        }
    ).dropna(subset=["date", "in_b", "out_b"])

    data["time_valid"] = data["time_valid"].fillna(0).astype(int)
    data["in_b"] = data["in_b"].astype(int)
    data["out_b"] = data["out_b"].astype(int)
    unique_dates = sorted({d for d in data["date"]})

    helper = _ExcelReportHelper(
        crossroad_path=crossroad_csv,
        image_path=crossroad_img,
        performance_path=performance_csv,
        clean_df=data,
        unique_dates=unique_dates,
    )
    helper.crossroad_df = df_cross
    helper.create(output_xlsx)

def run_batch(jobs: list[dict]) -> int:
    global BATCH_MODE_ACTIVE
    BATCH_MODE_ACTIVE = True

    print("=== 32_crossroad_report (batch mode) ===")
    print(f"jobs: {len(jobs)}")

    ok = 0
    skipped = 0
    failed = 0

    for idx, job in enumerate(jobs, start=1):
        try:
            crossroad_csv = Path(job["crossroad_csv"])
            crossroad_img = Path(job["crossroad_img"])
            performance_csv = Path(job["performance_csv"])

            # 出力先（省略時は performance_csv の隣に *_report.xlsx）
            if "output_xlsx" in job and str(job["output_xlsx"]).strip():
                output_xlsx = Path(job["output_xlsx"])
            else:
                output_xlsx = performance_csv.with_name(f"{performance_csv.stem}_report.xlsx")

            print(f"\n[{idx}/{len(jobs)}]")
            print(f"  crossroad_csv : {crossroad_csv}")
            print(f"  crossroad_img : {crossroad_img}")
            print(f"  performance   : {performance_csv}")
            print(f"  output_xlsx   : {output_xlsx}")

            # 存在チェック（足りない場合は次へ）
            missing = [p for p in [crossroad_csv, crossroad_img, performance_csv] if not p.exists()]
            if missing:
                print("  [SKIP] missing files:")
                for m in missing:
                    print(f"    - {m}")
                skipped += 1
                continue

            output_xlsx.parent.mkdir(parents=True, exist_ok=True)

            create_excel_report_headless(crossroad_csv, crossroad_img, performance_csv, output_xlsx)
            print("  [OK] saved excel")
            ok += 1

        except Exception as exc:
            print(f"  [ERROR] batch job failed: {exc}")
            print(traceback.format_exc())
            failed += 1
            continue

    print("\n=== batch finished ===")
    print(f"summary: ok={ok}, skipped={skipped}, failed={failed}, jobs={len(jobs)}")
    BATCH_MODE_ACTIVE = False

    if len(jobs) == 0:
        print("[ERROR] no jobs to process")
        return 2
    if failed > 0:
        print("[ERROR] batch finished with failures")
        return 1
    if ok == 0:
        print("[ERROR] batch finished with no successful reports")
        return 1
    return 0



def _list_crossroad_names(cross_dir: Path) -> list[str]:
    if not cross_dir.exists():
        return []
    return [p.stem for p in sorted(cross_dir.glob("*.csv"))]


def build_jobs_from_project(project_dir: Path, targets: list[str] | None) -> list[dict]:
    cross_dir = project_dir / FOLDER_CROSS
    perf_dir = project_dir / FOLDER_31OUT
    out_dir = project_dir / FOLDER_32OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    names = targets if targets else _list_crossroad_names(cross_dir)
    jobs: list[dict] = []
    for name in names:
        crossroad_csv = cross_dir / f"{name}.csv"
        crossroad_img = cross_dir / f"{name}.jpg"
        performance_csv = perf_dir / f"{name}_performance.csv"
        output_xlsx = out_dir / f"{name}_report.xlsx"

        missing = [p for p in [crossroad_csv, crossroad_img, performance_csv] if not p.exists()]
        if missing:
            print(f"[SKIP] {name}: missing files")
            for m in missing:
                print(f"  - {m}")
            continue

        jobs.append(
            {
                "crossroad_csv": str(crossroad_csv),
                "crossroad_img": str(crossroad_img),
                "performance_csv": str(performance_csv),
                "output_xlsx": str(output_xlsx),
            }
        )
    return jobs



def main() -> None:
    parser = argparse.ArgumentParser(description="32_crossroad_report (headless only)")
    parser.add_argument("--project", type=str, help="プロジェクトフォルダ")
    parser.add_argument("--targets", nargs="*", help="交差点名（stem）")
    args = parser.parse_args()

    if args.project:
        jobs = build_jobs_from_project(Path(args.project).resolve(), args.targets)
        code = run_batch(jobs)
        sys.exit(code)

    if BATCH_JOBS:
        code = run_batch(BATCH_JOBS)
        sys.exit(code)

    print("[ERROR] headless only: specify --project or define BATCH_JOBS")
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("[ERROR] unhandled exception in 32_crossroad_report.py")
        print(f"[ERROR] {exc}")
        traceback.print_exc()
        raise

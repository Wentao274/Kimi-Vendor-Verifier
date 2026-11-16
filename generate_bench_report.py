#!/usr/bin/env python3
"""Generate an HTML summary report for KVV benchmark runs.

Reads ``<bench_root>/<bench>/<effort>/summary.json`` files (written by
``kvv_bench_runner.py run`` / ``beam-summary``) and renders a standalone
HTML report, visually consistent with the functional validation report
(``generate_test_report.py``). Standard library only.

    uv run --no-sync python generate_bench_report.py \
        --bench-root bench_builds/t/kimi-k3/7/bench \
        --model kimi-k3 --base-url http://127.0.0.1:30000/v1 \
        --build-number 7
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

CSS = """
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Microsoft YaHei',
               'PingFang SC', sans-serif;
       margin: 0 auto; max-width: 1080px; padding: 28px 32px 44px;
       color: #1f2937; background: #eef1f6; font-size: 14px; line-height: 1.7; }
h1 { font-size: 24px; font-weight: 700; color: #0f172a; margin: 0 0 4px;
     padding-bottom: 10px; border-bottom: 3px solid #2563eb; }
h2 { font-size: 17px; font-weight: 600; color: #1e40af; margin-top: 34px;
     padding-left: 10px; border-left: 4px solid #2563eb; }
h3 { font-size: 15px; font-weight: 600; color: #334155; margin: 20px 0 6px; }
table { border-collapse: collapse; width: 100%; margin: 14px 0 18px;
        background: #ffffff; font-size: 13px;
        border: 1px solid #d8dfeb; box-shadow: 0 1px 4px rgba(15, 23, 42, .06); }
th { background: linear-gradient(180deg, #f2f7ff, #e3edfc); color: #1e3a8a;
     font-weight: 600; }
th, td { border: 1px solid #d8dfeb; padding: 6px 12px; text-align: left;
         vertical-align: top; }
tbody tr:nth-child(even) td { background: #f7fafd; }
tbody tr:hover td { background: #eef5ff; }
.meta { box-shadow: none; }
.meta td { border: none; padding: 3px 10px; }
.meta td:first-child { color: #64748b; width: 280px; }
.ok { color: #047857; font-weight: 600; }
.bad { color: #b91c1c; font-weight: 600; }
.warn { color: #b45309; font-weight: 600; }
.acc { color: #1e3a8a; font-weight: 700; }
p.note { color: #6b7280; font-size: 12px; margin: -10px 0 18px; }
footer { color: #6b7280; font-size: 12px; margin-top: 36px; padding-top: 12px;
         border-top: 1px solid #d1d5db; }
"""

STATUS_COLOR = {"success": "ok", "partial": "warn"}


def fmt_time(seconds: Any) -> str:
    try:
        s = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "N/A"
    if s >= 3600:
        return f"{s // 3600} 时 {(s % 3600) // 60} 分 {s % 60} 秒"
    if s >= 60:
        return f"{s // 60} 分 {s % 60} 秒"
    return f"{s} 秒"


def load_rows(bench_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in sorted(bench_root.glob("*/*/summary.json")):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARN: skip unreadable summary {p}: {exc}")
    return rows


def _first_metric(row: dict[str, Any], metric: str) -> Any:
    for score in row.get("scores", {}).values():
        if isinstance(score, dict) and score.get(metric) is not None:
            return score[metric]
    return None


def _num(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _combo(row: dict[str, Any]) -> str:
    return f"{row.get('bench', '?')} / {row.get('effort', '?')}"


def _status_cell(status: Any) -> str:
    cls = STATUS_COLOR.get(str(status), "bad")
    return f"<td class=\"{cls}\">{escape(str(status or 'unknown'))}</td>"


def build_report(rows: list[dict[str, Any]], model: str, base_url: str,
                 build_number: str, timestamp: str) -> str:
    total = len(rows)
    by_status: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status") or "unknown")
        by_status[key] = by_status.get(key, 0) + 1
    total_dur = sum(float(r.get("duration_s") or 0) for r in rows)
    status_summary = " · ".join(f"{k} {v}" for k, v in sorted(by_status.items())) or "无"

    ts = timestamp or datetime.now().strftime("%Y%m%d%H%M%S")
    run_at = (f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:{ts[12:14]}"
              if len(ts) >= 14 else ts)

    parts: list[str] = []
    p = parts.append

    p("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    p("<title>KVV 基准评测报告</title>")
    p(f"<style>{CSS}</style></head><body>")
    p("<h1>KVV 基准评测报告</h1>")

    # ---- 运行信息 ----
    p("<h2>运行信息</h2>")
    p("<table class='meta'>")
    p(f"<tr><td>模型</td><td>{escape(model or 'N/A')}</td></tr>")
    p(f"<tr><td>Base URL</td><td>{escape(base_url or 'N/A')}</td></tr>")
    if build_number:
        p(f"<tr><td>构建编号</td><td>#{escape(build_number)}</td></tr>")
    p(f"<tr><td>评测组合数</td><td>{total}（{escape(status_summary)}）</td></tr>")
    p(f"<tr><td>累计评测耗时</td><td>{fmt_time(total_dur)}</td></tr>")
    p(f"<tr><td>报告生成时间</td><td>{escape(run_at)}</td></tr>")
    p("</table>")

    # ---- 一、总览 ----
    p("<h2>一、总览</h2>")
    p("<table><tr><th>基准</th><th>effort</th><th>accuracy</th><th>stderr</th>"
      "<th>完成 / 总数</th><th>状态</th><th>耗时</th><th>样本上限</th></tr>")
    for row in rows:
        done, tot = row.get("completed_samples"), row.get("total_samples")
        done_s = "?" if done is None else str(done)
        tot_s = "?" if tot is None else str(tot)
        limit = row.get("limit")
        p(f"<tr><td>{escape(str(row.get('bench', '?')))}</td>"
          f"<td>{escape(str(row.get('effort', '?')))}</td>"
          f"<td class='acc'>{_num(_first_metric(row, 'accuracy'))}</td>"
          f"<td>{_num(_first_metric(row, 'stderr'))}</td>"
          f"<td>{done_s} / {tot_s}</td>"
          + _status_cell(row.get("status"))
          + f"<td>{fmt_time(row.get('duration_s'))}</td>"
          + f"<td>{'全量' if limit in (None, 0) else limit}</td></tr>")
    p("</table>")
    p("<p class='note'>accuracy 为 scorer 输出（inspect-ai scorer，或 BEAM 的 LLM-as-Judge 均分）；"
      "effort=legacy 表示未设置 KVB_EFFORT 的传统 --thinking 模式；"
      "status=partial 表示部分样本未完成（详见对应 bench 日志）。</p>")

    # ---- 二、运行配置 ----
    p("<h2>二、运行配置</h2>")
    p("<table><tr><th>组合</th><th>ThinkMode</th><th>MaxTokens</th><th>温度</th>"
      "<th>topP</th><th>epochs</th><th>并发</th><th>流式</th><th>BEAM</th></tr>")
    for row in rows:
        bench = str(row.get("bench", "?"))
        is_beam = bench == "beam"
        beam_cell = ("—" if not is_beam else
                     f"{escape(str(row.get('beam_data', '?')))} / "
                     f"{'plain' if row.get('plain_text') else 'multiturn'}")
        conn = "—" if is_beam else str(row.get("max_connections", "?"))
        stream = "—" if is_beam else ("是" if row.get("stream") else "否")
        epochs = "—" if is_beam else str(row.get("epochs", "?"))
        p(f"<tr><td>{escape(_combo(row))}</td>"
          f"<td>{escape(str(row.get('think_mode', '?')))}</td>"
          f"<td>{escape(str(row.get('max_tokens') or '?'))}</td>"
          f"<td>{escape(str(row.get('temperature', '?')))}</td>"
          f"<td>{escape(str(row.get('top_p', '?')))}</td>"
          f"<td>{epochs}</td><td>{conn}</td><td>{stream}</td>"
          f"<td>{beam_cell}</td></tr>")
    p("</table>")

    # ---- 三、评分明细 ----
    p("<h2>三、评分明细</h2>")
    score_rows: list[str] = []
    for row in rows:
        for scorer, metrics in (row.get("scores") or {}).items():
            if not isinstance(metrics, dict):
                continue
            for metric, value in metrics.items():
                score_rows.append(
                    f"<tr><td>{escape(_combo(row))}</td>"
                    f"<td>{escape(str(scorer))}</td>"
                    f"<td>{escape(str(metric))}</td>"
                    f"<td>{_num(value)}</td></tr>")
    if score_rows:
        p("<table><tr><th>组合</th><th>scorer</th><th>指标</th><th>数值</th></tr>")
        for line in score_rows:
            p(line)
        p("</table>")
    else:
        p("<p>（无评分数据）</p>")

    # ---- 四、BEAM 能力维度 ----
    beam_rows = [r for r in rows if str(r.get("bench")) == "beam"]
    if beam_rows:
        p("<h2>四、BEAM 能力维度</h2>")
        for row in beam_rows:
            p(f"<h3>{escape(_combo(row))}</h3>")
            per_ability = row.get("per_ability") or {}
            if per_ability:
                p("<table><tr><th>能力维度</th><th>accuracy</th></tr>")
                for ability, acc in per_ability.items():
                    p(f"<tr><td>{escape(str(ability))}</td>"
                      f"<td class='acc'>{_num(acc)}</td></tr>")
                p("</table>")
            gen_err = row.get("generation_errors")
            p(f"<p class='note'>生成阶段错误 {gen_err if gen_err is not None else 'N/A'} 个；"
              f"生成耗时 {fmt_time(row.get('gen_duration_s'))}，"
              f"判分耗时 {fmt_time(row.get('judge_duration_s'))}。</p>")

    p("<p class='note'>各组合完整日志见同目录 bench_&lt;bench&gt;_&lt;effort&gt;.log 与 "
      "bench_aggregate.log；构建全程控制台输出见附件 bench_run 日志。</p>")
    p(f"<footer>Generated by generate_bench_report.py at "
      f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</footer>")
    p("</body></html>")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate KVV bench HTML report")
    parser.add_argument("--bench-root", required=True, type=Path,
                        help="Bench root containing <bench>/<effort>/summary.json")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--build-number", default="")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--output", default=None,
                        help="Output HTML file path")
    args = parser.parse_args()

    rows = load_rows(args.bench_root)
    if not rows:
        print(f"error: no summary.json found under {args.bench_root}")
        return 1

    html = build_report(rows, args.model, args.base_url,
                        args.build_number, args.timestamp)

    ts = args.timestamp or datetime.now().strftime("%Y%m%d%H%M%S")
    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "_", (args.model or "").strip()).strip("._-") or "unknown"
    default_name = f"{safe_model}_kvv_bench_test_report_{ts}.html"
    output = Path(args.output) if args.output else args.bench_root / default_name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(f"bench report written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

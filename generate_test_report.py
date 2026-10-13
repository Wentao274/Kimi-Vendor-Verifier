#!/usr/bin/env python3
"""Generate an HTML summary report from KVV pytest JUnit XML and JSON reports.

Standard library only (no external dependencies):

    uv run --no-sync python generate_test_report.py \
        --out-dir outputs/kimi-k3_20261012122237 \
        --model kimi-k3 --base-url http://127.0.0.1:30000/v1 \
        --timestamp 20261012122237
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

JUNIT_FILES = [
    "junit_params_opensource.xml",
    "junit_params_kimi.xml",
    "junit_main.xml",
    "junit.xml",
]

RUN_LABELS = {
    "junit_params_opensource.xml": "params (opensource)",
    "junit_params_kimi.xml": "params (kimi)",
    "junit_main.xml": "主套件",
    "junit.xml": "主套件",
}

CLASS_LABELS = {
    "params/test_params": "params（参数约束）",
    "k3_features/test_dynamic_tools": "k3_features/动态工具",
    "k3_features/test_tool_choice": "k3_features/tool_choice",
    "k3_features/test_thinking_effort": "k3_features/thinking effort",
    "k3_features/test_response_format": "k3_features/response_format",
    "k3_features/test_tokenization_groundtruth": "k3_features/分词基准",
    "tool_call_json_schema/test_tool_call_json_schema": "tool_call_json_schema",
    "prompt_tokens/test_prompt_tokens": "prompt_tokens",
}


@dataclass
class CaseOutcome:
    kind: str
    classname: str
    name: str
    message: str


@dataclass
class ClassStats:
    name: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    time: float = 0.0


@dataclass
class RunResult:
    label: str
    junit_file: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    time: float = 0.0
    per_class: dict[str, ClassStats] = field(default_factory=dict)
    outcomes: list[CaseOutcome] = field(default_factory=list)


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _shorten(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _strip_mode(name: str) -> str:
    return re.sub(r"\[[^\]]*\]$", "", name)


_ZH_EXACT: dict[str, str] = {
    "collection failure": "收集失败（测试模块导入失败，请检查 testdata 文件）",
    "temporarily skipped: reasoning length is noisy": "临时跳过：reasoning 长度噪声较大，比较不稳定",
    "skip thinking disabled, tc assert not right": "跳过：thinking 禁用时断言不正确",
    "skip, not pass": "跳过：暂未通过",
    "skip, tc not pass": "跳过：token 计数暂未通过",
    "online can use preset or normalization to force keep='all'; skip the keep != 'all' rejection check":
        "线上可通过 preset/normalization 强制 keep='all'，跳过 keep != 'all' 拒绝检查",
    "current behavior silently ignores named-function tool_choice, treating it as no tool_choice":
        "当前行为会静默忽略命名函数 tool_choice（视为未设置）",
}

_ZH_REGEX: list[tuple[str, str]] = [
    (r"^AssertionError: prompt_tokens=(\d+), expected=(\d+) assert \d+ == \d+$",
     r"断言失败：实际 prompt_tokens=\1，期望 \2"),
    (r"^AssertionError: prompt_tokens=\d+ must be between \d+ and \d+.*$",
     r"断言失败：prompt_tokens 超出容差范围"),
    (r"^AssertionError: expected '([^']*)' in effort report, got: '([^']*)'.*$",
     r"断言失败：期望 effort 报告为 '\1'，实际为 '\2'"),
    (r"^AssertionError: reasoning_effort should be ignored when thinking\.effort present: with_ignored=(\d+), max_only=(\d+).*$",
     r"断言失败：thinking.effort 存在时应忽略 reasoning_effort（with_ignored=\1，max_only=\2）"),
]

_ZH_PARTIAL: list[tuple[str, str]] = [
    ("AssertionError: ", "断言失败："),
    ("tool schema rejected", "工具 schema 被拒绝"),
    ("Error code: 400", "HTTP 400 错误"),
    ("Tool 0 function has invalid 'parameters' schema", "工具 0 的 'parameters' schema 无效"),
    ("does not match", "不匹配"),
    ("collection failure", "收集失败"),
]


def _zh(text: str) -> str:
    """Best-effort translation of common English skip/failure messages."""
    if not text:
        return text
    stripped = text.strip()
    if stripped in _ZH_EXACT:
        return _ZH_EXACT[stripped]
    for pattern, repl in _ZH_REGEX:
        if re.match(pattern, stripped):
            return re.sub(pattern, repl, stripped)
    for src, dst in _ZH_PARTIAL:
        text = text.replace(src, dst)
    return text


def _class_display(classname: str) -> str:
    name = classname.strip()
    if not name:
        return "收集错误"
    if name.startswith("tests."):
        name = name[len("tests."):]
    return name.replace(".", "/")


def _params_mode(junit_file: str) -> str | None:
    """Think-mode tag of a params junit file (opensource / kimi), else None."""
    m = re.search(r"junit_params_([A-Za-z0-9_]+)\.xml$", junit_file)
    return m.group(1) if m else None


def parse_junit(path: Path, label: str) -> RunResult:
    run = RunResult(label=label, junit_file=path.name)
    root = ET.parse(path).getroot()
    for ts in root.iter("testsuite"):
        run.time += _f(ts.get("time"))
        for tc in ts.iter("testcase"):
            classname = (tc.get("classname") or "").strip()
            name = tc.get("name") or ""
            display = _class_display(classname or name)
            cs = run.per_class.setdefault(display, ClassStats(name=display))
            cs.time += _f(tc.get("time"))
            children = [el for el in tc if el.tag in ("failure", "error", "skipped")]
            if not children:
                run.total += 1
                run.passed += 1
                cs.total += 1
                cs.passed += 1
                continue
            for el in children:
                msg = _shorten(el.get("message") or (el.text or "").strip())
                run.total += 1
                cs.total += 1
                if el.tag == "failure":
                    run.failed += 1
                    cs.failed += 1
                elif el.tag == "error":
                    run.errors += 1
                    cs.errors += 1
                else:
                    run.skipped += 1
                    cs.skipped += 1
                run.outcomes.append(CaseOutcome(el.tag, display, name, msg))
    return run


def fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    if s >= 3600:
        return f"{s // 3600} 时 {(s % 3600) // 60} 分 {s % 60} 秒"
    if s >= 60:
        return f"{s // 60} 分 {s % 60} 秒"
    return f"{s} 秒"


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
p.note { color: #6b7280; font-size: 12px; margin: -10px 0 18px; }
ul { padding-left: 22px; }
li { margin: 4px 0; }
details { margin: 8px 0; }
summary { cursor: pointer; font-weight: 600; color: #1d4ed8; }
footer { color: #6b7280; font-size: 12px; margin-top: 36px; padding-top: 12px;
         border-top: 1px solid #d1d5db; }
"""


def td_num(v: int | float) -> str:
    return f"<td>{v}</td>" if isinstance(v, int) else f"<td>{v:g}</td>"


def build_report(runs: list[RunResult], tool_json: dict[str, Any], model: str,
                 base_url: str, timestamp: str) -> str:
    # ---- 聚合统计 ----
    total = sum(r.total for r in runs)
    passed = sum(r.passed for r in runs)
    failed = sum(r.failed for r in runs)
    skipped = sum(r.skipped for r in runs)
    errors = sum(r.errors for r in runs)
    rate = passed / total * 100 if total else 0.0

    ts = timestamp or datetime.now().strftime("%Y%m%d%H%M%S")
    run_at = (f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:{ts[12:14]}"
              if len(ts) >= 14 else ts)

    parts: list[str] = []
    p = parts.append

    # ---- HTML 头部与样式 ----
    p("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    p("<title>KVV 测试结果汇总报告</title>")
    p(f"<style>{CSS}</style></head><body>")
    p("<h1>KVV 测试结果汇总报告</h1>")

    # ---- 运行信息 ----
    p("<h2>运行信息</h2>")
    p("<table class='meta'>")
    p(f"<tr><td>模型</td><td>{escape(model)}</td></tr>")
    p(f"<tr><td>Base URL</td><td>{escape(base_url)}</td></tr>")
    p(f"<tr><td>运行编号</td><td>{escape(ts)}</td></tr>")
    p(f"<tr><td>运行时间</td><td>{escape(run_at)}</td></tr>")
    for run in runs:
        p(f"<tr><td>运行「{escape(run.label)}」</td>"
          f"<td>用例 {run.total}，耗时 {fmt_time(run.time)}</td></tr>")
    p(f"<tr><td>汇总报告生成时间</td>"
      f"<td>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>")
    p("</table>")

    # ---- 一、总览 ----
    p("<h2>一、总览</h2>")
    p("<table><tr><th>运行</th><th>用例数</th><th>通过</th><th>失败</th>"
      "<th>跳过</th><th>错误</th><th>耗时</th><th>结果</th></tr>")
    ok = failed == 0 and errors == 0
    p("<tr><td>主套件</td>" + td_num(total) + td_num(passed)
      + td_num(failed) + td_num(skipped) + td_num(errors)
      + f"<td>{fmt_time(sum(r.time for r in runs))}</td>"
      + f"<td class=\"{'ok' if ok else 'bad'}\">{'✔ 通过' if ok else '✘ 失败'}</td></tr>")
    p("</table>")
    p("<table><tr><th>指标</th><th>数值</th></tr>"
      f"<tr><td>用例总数</td><td>{total}</td></tr>"
      f"<tr><td>通过</td><td>{passed}</td></tr>"
      f"<tr><td>失败</td><td>{failed}</td></tr>"
      f"<tr><td>跳过（上游有意跳过）</td><td>{skipped}</td></tr>"
      f"<tr><td>收集错误</td><td>{errors}</td></tr>"
      f"<tr><td>通过率</td><td>{rate:.2f}%（{passed}/{total}）</td></tr>"
      "</table>")

    # ---- 二、按套件统计 ----
    p("<h2>二、按套件统计</h2>")
    merged: dict[str, ClassStats] = {}
    params_keys: set[str] = set()
    for run in runs:
        mode = _params_mode(run.junit_file)
        for cs in run.per_class.values():
            key = f"{cs.name}（{mode} 模式）" if mode else cs.name
            agg = merged.setdefault(key, ClassStats(name=key))
            if mode:
                params_keys.add(key)
            agg.total += cs.total
            agg.passed += cs.passed
            agg.failed += cs.failed
            agg.skipped += cs.skipped
            agg.errors += cs.errors
            agg.time += cs.time
    if not merged:
        p("<p>（无收集结果）</p>")
    else:
        p("<table><tr><th>套件</th><th>用例数</th><th>通过</th><th>失败</th>"
          "<th>跳过</th><th>错误</th><th>累计用时*</th></tr>")
        for cs in sorted(merged.values(),
                         key=lambda c: (c.name not in params_keys, c.name)):
            p(f"<tr><td>{escape(cs.name)}</td>" + td_num(cs.total) + td_num(cs.passed)
              + td_num(cs.failed) + td_num(cs.skipped) + td_num(cs.errors)
              + f"<td>{fmt_time(cs.time)}</td></tr>")
        p("</table>")
        p("<p class='note'>* 并行运行时累计用时为各用例耗时之和，可能大于总耗时。</p>")

    # ---- 三、失败 / 错误 / 跳过明细 ----
    p("<h2>三、失败 / 错误 / 跳过明细</h2>")
    fails = [o for r in runs for o in r.outcomes if o.kind in ("failure", "error")]
    skips = [o for r in runs for o in r.outcomes if o.kind == "skipped"]
    if fails or skips:
        p("<h3>主套件</h3>")
        if fails:
            p("<table><tr><th>结果</th><th>测试用例</th><th>错误摘要</th></tr>")
            for o in fails:
                label = "失败" if o.kind == "failure" else "错误"
                full = f"{o.classname}::{o.name}"
                p(f"<tr class='bad'><td>{label}</td>"
                  f"<td>{escape(full)}</td><td>{escape(_zh(o.message))}</td></tr>")
            p("</table>")
        if skips:
            p("<details open><summary>跳过用例（点击折叠）</summary>")
            p("<table><tr><th>测试用例</th><th>数量</th><th>原因</th></tr>")
            grouped: dict[tuple[str, str], list[CaseOutcome]] = {}
            for o in skips:
                key = (o.classname, _strip_mode(o.name))
                grouped.setdefault(key, []).append(o)
            for (cls_name, base), items in sorted(grouped.items()):
                reasons = {o.message or "—" for o in items}
                reason = " / ".join(sorted(reasons))
                p(f"<tr><td>{escape(f'{cls_name}::{base}')}</td>"
                  f"<td>{len(items)}</td><td>{escape(_zh(reason))}</td></tr>")
            p("</table></details>")
    else:
        p("<p class='good'>无失败、错误或跳过用例。</p>")

    # ---- 四、tool_call 用例细分 ----
    if tool_json:
        p("<h2>四、tool_call 用例细分</h2>")
        summary = tool_json.get("summary", {})
        by_mode = summary.get("by_mode", {})
        if by_mode:
            keys = sorted({k for m in by_mode.values() for k in m})
            p("<table><tr><th>模式</th>"
              + "".join(f"<th>{escape(k)}</th>" for k in keys) + "</tr>")
            for mode, stats in sorted(by_mode.items()):
                p(f"<tr><td>{escape(mode)}</td>"
                  + "".join(f"<td>{stats.get(k, 0)}</td>" for k in keys)
                  + "</tr>")
            p("</table>")
        by_reason = summary.get("by_selection_reason", {})
        if by_reason:
            p("<table><tr><th>selection_reason</th><th>用例数</th></tr>")
            for k, v in sorted(by_reason.items()):
                p(f"<tr><td>{escape(k)}</td><td>{v}</td></tr>")
            p("</table>")
        failed_cases = [r for r in tool_json.get("results", []) if r.get("status") != "passed"]
        if failed_cases:
            p("<table><tr><th>失败用例</th><th>模式</th></tr>")
            for r in failed_cases:
                case_id = f"{r.get('suite')}:{r.get('line')}"
                p(f"<tr class='bad'><td>{escape(case_id)}</td>"
                  f"<td>{escape(str(r.get('mode', '')))}</td></tr>")
            p("</table>")

    # ---- 五、结论 ----
    p("<h2>五、结论</h2>")
    p("<ul>")
    p(f"<li>通过率 <b>{rate:.2f}%</b>（{passed}/{total}）。</li>")
    if failed:
        p(f"<li>失败 <b>{failed}</b> 项，涉及套件：")
        p("、".join(sorted({o.classname for r in runs for o in r.outcomes
                          if o.kind == "failure"})))
        p("。</li>")
    else:
        p("<li>无失败用例。</li>")
    if errors:
        p(f"<li>收集错误 <b>{errors}</b> 项（请检查对应 testdata 文件是否损坏或未拉取 LFS）。</li>")
    if skipped:
        p(f"<li>跳过 <b>{skipped}</b> 项（测试代码有意跳过，非服务问题）。</li>")
    p("</ul>")

    p(f"<footer>Generated by generate_test_report.py at "
      f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</footer>")
    p("</body></html>")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="Directory containing junit_*.xml and tool_call_schema_report.json")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--output", default=None,
                        help="Output HTML file path")
    args = parser.parse_args()

    out_dir: Path = args.out_dir
    runs: list[RunResult] = []
    seen: set[str] = set()
    for junit in JUNIT_FILES:
        path = out_dir / junit
        if path.is_file():
            runs.append(parse_junit(path, RUN_LABELS.get(junit, junit)))
            seen.add(junit)
    if not runs:
        print("error: no junit xml found in", out_dir)
        return 1

    tool_json: dict[str, Any] = {}
    tool_path = out_dir / "tool_call_schema_report.json"
    if tool_path.is_file():
        try:
            tool_json = json.loads(tool_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            tool_json = {}

    html = build_report(runs, tool_json, args.model, args.base_url, args.timestamp)

    default_name = f"kkv_base_test_{args.timestamp}_report.html" if args.timestamp else "kkv_base_test_report.html"
    output = Path(args.output) if args.output else out_dir / default_name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(f"report written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

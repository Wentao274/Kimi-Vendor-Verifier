"""KVV benchmark runner.

Wraps the upstream inspect-ai tasks (ocrbench / mmmu / kimi_toolcall) with two
things the upstream eval.py CLI does not provide:

  1. ``--limit``            — the task constructors accept a sample limit, but
                              eval.py only exposes it for kimi_toolcall; a
                              full MMMU run (~11.7k samples) is not viable in
                              CI, so the limit must be reachable from the CLI.
  2. machine-readable summary — eval.py prints for humans only. This runner
                              writes ``summary.json`` per (bench, effort) run
                              and an aggregate ``bench_summary.properties``
                              for the Jenkins email stage.

Task definitions, thinking-extra-body semantics and recommended parameters
come from the upstream files; this runner only orchestrates them.

The ``beam-summary`` subcommand aggregates the standalone BEAM (1M) two-phase
scripts (beam_generate.py / beam_judge.py) into the same summary.json format,
so BEAM rows flow into ``bench_summary.properties`` like the other benches.

Run a single (bench, effort) combination:

    uv run --no-sync python kvv_bench_runner.py \
        --bench mmmu --model kimi-k3 --effort max --limit 100 \
        --max-tokens 98304 --out-dir builds/t/1/bench/mmmu/max

Aggregate all runs under a bench root directory:

    uv run --no-sync python kvv_bench_runner.py --aggregate builds/t/1/bench
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import kimi_model  # noqa: F401  (import registers the kimi/ provider)
from inspect_ai import eval

from eval import get_thinking_extra_body
from kimi_toolcall import kimi_toolcall
from mmmu_pro_vision import mmmu_pro_10c
from ocr_bench import ocrbench

BENCHMARKS = ("ocrbench", "mmmu", "kimi_toolcall")
EFFORTS = ("none", "low", "high", "max")
TOOLCALL_DEFAULT_DATASET = "toolcall_benchmark/toolcall_thinking_samples.stable2.jsonl"


def _build_task(args: argparse.Namespace):
    limit = args.limit if args.limit and args.limit > 0 else None
    if args.bench == "ocrbench":
        return ocrbench(limit=limit)
    if args.bench == "mmmu":
        return mmmu_pro_10c(limit=limit)
    if args.bench == "kimi_toolcall":
        dataset = args.dataset or TOOLCALL_DEFAULT_DATASET
        if not Path(dataset).is_file():
            raise SystemExit(
                f"kimi_toolcall dataset not found: {dataset} "
                f"(git lfs pull first)"
            )
        return kimi_toolcall(dataset_path=dataset, limit=limit)
    raise SystemExit(f"unknown benchmark: {args.bench}")


def _extract_summary(log: Any, args: argparse.Namespace, extra_body: dict,
                     duration_s: float) -> dict[str, Any]:
    results = getattr(log, "results", None)
    scores: dict[str, dict[str, float]] = {}
    total = completed = None
    if results is not None:
        total = getattr(results, "total_samples", None)
        completed = getattr(results, "completed_samples", None)
        for score in getattr(results, "scores", None) or []:
            scores[str(score.name)] = {
                str(metric.name): metric.value
                for metric in (getattr(score, "metrics", None) or {}).values()
            }
    return {
        "bench": args.bench,
        "effort": args.effort or "legacy",
        "model": args.model,
        "think_mode": args.think_mode,
        "thinking": args.thinking,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "limit": args.limit or None,
        "epochs": args.epochs,
        "max_connections": args.max_connections,
        "stream": args.stream,
        "status": str(getattr(log, "status", "unknown")),
        "total_samples": total,
        "completed_samples": completed,
        "scores": scores,
        "extra_body": extra_body,
        "duration_s": round(duration_s, 1),
        "log_file": str(getattr(log, "location", "")),
    }


_PROXY_ENV_VARS = (
    "https_proxy", "http_proxy", "all_proxy",
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
)


def _strip_proxy_env() -> list[str]:
    """Remove proxy env vars from this process before evaluation starts.

    Datasets are already loaded by ``_build_task`` (HF download / local LFS
    file); every request from here on goes to the model API and must NOT
    traverse any proxy (httpx/openai honor these env vars via trust_env).
    Returns the removed ``var=value`` entries for logging.
    """
    removed = []
    for var in _PROXY_ENV_VARS:
        val = os.environ.pop(var, None)
        if val:
            removed.append(f"{var}={val}")
    return removed


def run_benchmark(args: argparse.Namespace) -> int:
    task = _build_task(args)  # dataset load/download happens here (proxy OK)
    removed = _strip_proxy_env()
    if removed:
        print("proxy env stripped after dataset load (evaluation runs direct):")
        for item in removed:
            print(f"  unset {item}")
    model = args.model if "/" in args.model else f"kimi/{args.model}"
    extra_body = get_thinking_extra_body(
        thinking=args.thinking,
        mode=args.think_mode,
        thinking_effort=args.effort,
    )

    print("=" * 60)
    print(f"KVV bench: {args.bench} | effort={args.effort or 'legacy'} | "
          f"think_mode={args.think_mode}")
    print(f"model={model} | limit={args.limit or 'FULL'} | "
          f"max_tokens={args.max_tokens} | epochs={args.epochs} | "
          f"connections={args.max_connections} | stream={args.stream}")
    print(f"extra_body={extra_body}")
    print("=" * 60)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    results = eval(
        [task],
        model,
        max_tokens=args.max_tokens,
        max_connections=args.max_connections,
        epochs=args.epochs,
        extra_body=extra_body,
        retry_on_error=3,
        continue_on_fail=True,
        fail_on_error=False,
        temperature=args.temperature,
        top_p=args.top_p,
        log_dir=str(out_dir / "inspect_logs"),
        model_args={
            "stream": args.stream,
            "max_retries": 0,
            "timeout": args.client_timeout,
            "use_max_completion_tokens": False,
        },
    )
    duration = time.time() - started

    log = results[0] if isinstance(results, list) else results
    summary = _extract_summary(log, args, extra_body, duration)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"summary written to: {summary_path}")
    return 0 if summary["status"] == "success" else 1


def _first_metric(summary: dict, metric: str) -> float | None:
    for score in summary.get("scores", {}).values():
        if metric in score:
            return score[metric]
    return None


def beam_summary(args: argparse.Namespace) -> int:
    """Aggregate BEAM answers/scores into a summary.json compatible with
    ``aggregate`` (bench/<bench>/<effort>/summary.json glob), so BEAM rows
    flow into bench_summary.properties and the Jenkins email table."""

    def read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    answers = read_jsonl(Path(args.answers))
    scored = read_jsonl(Path(args.scores))
    if not answers:
        print(f"ERROR: no answers found in {args.answers}")
        return 1
    if not scored:
        print(f"ERROR: no scores found in {args.scores}")
        return 1

    per_ability: dict[str, list[float]] = {}
    for r in scored:
        if isinstance(r.get("score"), (int, float)):
            per_ability.setdefault(str(r.get("question_type", "unknown")),
                                   []).append(float(r["score"]))
    values = [v for vals in per_ability.values() for v in vals]
    if not values:
        print("ERROR: no numeric scores found")
        return 1

    accuracy = sum(values) / len(values)
    stderr = (statistics.stdev(values) / math.sqrt(len(values))
              if len(values) > 1 else 0.0)
    generation_errors = sum(1 for r in answers if r.get("error"))
    status = "success" if len(scored) >= len(answers) else "partial"

    summary = {
        "bench": "beam",
        "effort": args.effort,
        "model": args.model,
        "think_mode": args.think_mode,
        "beam_data": args.beam_data,
        "plain_text": bool(args.plain_text),
        "temperature": args.temperature,
        "max_tokens": args.max_tokens or None,
        "limit": args.limit or None,
        "status": status,
        "total_samples": len(answers),
        "completed_samples": len(scored),
        "generation_errors": generation_errors,
        "scores": {"beam_judge": {"accuracy": round(accuracy, 4),
                                  "stderr": round(stderr, 4)}},
        "per_ability": {k: round(sum(v) / len(v), 4)
                        for k, v in sorted(per_ability.items())},
        "gen_duration_s": round(args.gen_duration, 1),
        "judge_duration_s": round(args.judge_duration, 1),
        "duration_s": round(args.gen_duration + args.judge_duration, 1),
        "answers_file": args.answers,
        "scores_file": args.scores,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"BEAM overall: {accuracy:.4f} +/- {stderr:.4f} "
          f"({len(scored)}/{len(answers)} scored, "
          f"{generation_errors} generation errors, status={status})")
    for ability, vals in sorted(per_ability.items()):
        print(f"  {ability}: {sum(vals) / len(vals):.4f} (n={len(vals)})")
    print(f"summary written to: {summary_path}")
    return 0 if status == "success" else 1


def aggregate(root: str) -> int:
    root_path = Path(root)
    summary_files = sorted(root_path.glob("*/*/summary.json"))
    if not summary_files:
        print(f"no summary.json found under {root_path}")
        return 1

    rows = [json.loads(p.read_text(encoding="utf-8")) for p in summary_files]
    lines: list[str] = []
    print(f"{'bench':<16}{'effort':<10}{'accuracy':<10}{'stderr':<10}"
          f"{'done/total':<14}{'status':<10}")
    for row in rows:
        key = f"bench.{row['bench']}.{row['effort']}"
        accuracy = _first_metric(row, "accuracy")
        stderr = _first_metric(row, "stderr")
        done = row.get("completed_samples")
        total = row.get("total_samples")
        lines.append(f"{key}.accuracy={'' if accuracy is None else f'{accuracy:.4f}'}")
        lines.append(f"{key}.stderr={'' if stderr is None else f'{stderr:.4f}'}")
        lines.append(f"{key}.completed={done if done is not None else ''}")
        lines.append(f"{key}.total={total if total is not None else ''}")
        lines.append(f"{key}.status={row.get('status', '')}")
        lines.append(f"{key}.duration_s={row.get('duration_s', '')}")
        lines.append(f"{key}.log_file={row.get('log_file', '')}")
        print(f"{row['bench']:<16}{row['effort']:<10}"
              f"{'N/A' if accuracy is None else f'{accuracy:.4f}':<10}"
              f"{'N/A' if stderr is None else f'{stderr:.4f}':<10}"
              f"{'{}/{}'.format(done if done is not None else '?', total if total is not None else '?'):<14}"
              f"{row.get('status', ''):<10}")

    props_path = root_path / "bench_summary.properties"
    props_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"aggregate written to: {props_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="KVV benchmark runner")
    sub = parser.add_subparsers(dest="mode")

    run_parser = sub.add_parser("run", help="run a single (bench, effort) combo")
    run_parser.add_argument("--bench", required=True, choices=BENCHMARKS)
    run_parser.add_argument("--model", required=True)
    run_parser.add_argument("--think-mode", choices=["kimi", "opensource"],
                            default="kimi")
    run_parser.add_argument("--thinking", action="store_true",
                            help="legacy K2 thinking mode (ignored when --effort is set)")
    run_parser.add_argument("--effort", choices=EFFORTS, default=None,
                            help="reasoning_effort; unset = legacy --thinking mode")
    run_parser.add_argument("--limit", type=int, default=0,
                            help="sample limit (0 = full dataset)")
    run_parser.add_argument("--max-tokens", type=int, required=True)
    run_parser.add_argument("--temperature", type=float, default=1.0)
    run_parser.add_argument("--top-p", type=float, default=0.95)
    run_parser.add_argument("--epochs", type=int, default=1)
    run_parser.add_argument("--max-connections", type=int, default=8,
                            help="concurrent requests (SGLang: keep low, official API uses 50)")
    run_parser.add_argument("--client-timeout", type=int, default=86400)
    run_parser.add_argument("--stream", action="store_true")
    run_parser.add_argument("--dataset", default=None,
                            help=f"kimi_toolcall dataset (default: {TOOLCALL_DEFAULT_DATASET})")
    run_parser.add_argument("--out-dir", required=True,
                            help="output dir for this (bench, effort) combo")
    run_parser.set_defaults(func=run_benchmark)

    agg_parser = sub.add_parser("aggregate",
                                help="aggregate */summary.json under a bench root")
    agg_parser.add_argument("root")
    agg_parser.set_defaults(func=aggregate)

    bs_parser = sub.add_parser("beam-summary",
                               help="aggregate BEAM answers/scores into summary.json")
    bs_parser.add_argument("--answers", required=True,
                           help="answers.jsonl from beam_generate.py")
    bs_parser.add_argument("--scores", required=True,
                           help="scores.jsonl from beam_judge.py")
    bs_parser.add_argument("--out-dir", required=True,
                           help="output dir for summary.json (= bench/beam/<effort>)")
    bs_parser.add_argument("--effort", required=True,
                           help="effort label used for the run dir (e.g. max)")
    bs_parser.add_argument("--model", required=True)
    bs_parser.add_argument("--think-mode", default="kimi")
    bs_parser.add_argument("--beam-data", default="1m",
                           help="BEAM data tag (1m / 128k)")
    bs_parser.add_argument("--plain-text", action="store_true",
                           help="answers were generated in plain-text mode")
    bs_parser.add_argument("--temperature", type=float, default=1.0)
    bs_parser.add_argument("--max-tokens", type=int, default=0)
    bs_parser.add_argument("--limit", type=int, default=0)
    bs_parser.add_argument("--gen-duration", type=float, default=0.0,
                           help="generation phase duration in seconds")
    bs_parser.add_argument("--judge-duration", type=float, default=0.0,
                           help="judge phase duration in seconds")
    bs_parser.set_defaults(func=beam_summary)

    args = parser.parse_args()
    if args.mode == "run":
        sys.exit(args.func(args))
    elif args.mode == "aggregate":
        sys.exit(aggregate(args.root))
    elif args.mode == "beam-summary":
        sys.exit(args.func(args))
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()

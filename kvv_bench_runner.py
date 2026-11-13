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


def run_benchmark(args: argparse.Namespace) -> int:
    task = _build_task(args)
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

    args = parser.parse_args()
    if args.mode == "run":
        sys.exit(args.func(args))
    elif args.mode == "aggregate":
        sys.exit(aggregate(args.root))
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()

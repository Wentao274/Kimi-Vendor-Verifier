#!/usr/bin/env bash
set -euo pipefail

# ==================== Jenkins 触发的 KVV 测试入口 ====================
# 与本地脚本 run_preflight_tests.sh 相互独立，互不影响：
#   - run_preflight_tests.sh : 本地手工执行，配置固定写在脚本顶部
#   - run_kimivv_tests.sh    : 由 Jenkins 远程触发，配置全部通过环境变量注入，
#                             按 KVV_SUITE 选择执行套件，产出 junit XML 与
#                             generate_test_report.py 生成的 HTML 汇总报告
#                             （格式与本地完全一致），以及 stats.properties
#
# 环境变量（模型基础信息由 Jenkinsfile 参数透传）：
#   KIMI_BASE_URL     API 地址（以 /v1 结尾；Jenkinsfile 传入拼接好的 BASE_URL_V1）
#   KIMI_API_KEY      API Key（SGLang 未开启鉴权时填 EMPTY）
#   MODEL_NAME        模型名，即 /v1/models 返回的 id
#   KVV_SUITE         all | params | k3_features | prompt_tokens | tool_call_json_schema
#   KVV_THINK_MODE    主套件 thinking 参数格式：opensource | kimi | none
#   KVV_THINKING      主套件是否开启 thinking（--thinking，默认 true）
#   KVV_PARALLEL_JOBS pytest-xdist 并发数（默认 4）
#   KVV_RERUNS        失败重试次数（默认 3）
#   KVV_RERUNS_DELAY  重试间隔秒数（默认 2）
#   KVV_OUT_DIR       结果输出目录（默认 outputs/<model>_<timestamp>）
#   KVV_CASE_DIR      tool 套件 walle validator_cases 目录
#   KVV_SELECTION     tool 套件用例选择模式 all/explicit/object
#   KVV_MAX_CASES     tool 套件用例数上限（空 = 不限制）
#   KVV_MAX_TOKENS    tool 套件单次响应最大 tokens
# =======================================================

cd "$(dirname "$0")"

KIMI_BASE_URL="${KIMI_BASE_URL:-http://10.11.75.45:3000/v1}"
KIMI_API_KEY="${KIMI_API_KEY:-EMPTY}"
MODEL_NAME="${MODEL_NAME:-kimi-k3}"
KVV_SUITE="${KVV_SUITE:-all}"
KVV_THINK_MODE="${KVV_THINK_MODE:-opensource}"
KVV_THINKING="${KVV_THINKING:-true}"
KVV_PARALLEL_JOBS="${KVV_PARALLEL_JOBS:-4}"
KVV_RERUNS="${KVV_RERUNS:-3}"
KVV_RERUNS_DELAY="${KVV_RERUNS_DELAY:-2}"
KVV_OUT_DIR="${KVV_OUT_DIR:-}"
KVV_CASE_DIR="${KVV_CASE_DIR:-testdata/walle_validator_cases/validator_cases}"
KVV_SELECTION="${KVV_SELECTION:-all}"
KVV_MAX_CASES="${KVV_MAX_CASES:-}"
KVV_MAX_TOKENS="${KVV_MAX_TOKENS:-2048}"

case "${KVV_SUITE}" in
    all|params|k3_features|prompt_tokens|tool_call_json_schema) ;;
    *)
        echo "ERROR: invalid KVV_SUITE '${KVV_SUITE}'" >&2
        echo "       expected: all | params | k3_features | prompt_tokens | tool_call_json_schema" >&2
        exit 2
        ;;
esac

TIMESTAMP="$(date +%Y%m%d%H%M%S)"
MODEL_NAME_SAFE="${MODEL_NAME//\//_}"
if [ -n "${KVV_OUT_DIR}" ]; then
    OUT_DIR="${KVV_OUT_DIR}"
else
    OUT_DIR="outputs/${MODEL_NAME_SAFE}_${TIMESTAMP}"
fi
LOG_FILE="${OUT_DIR}/kkv_base_test_${TIMESTAMP}.log"
JUNIT_MAIN="${OUT_DIR}/junit_main.xml"
JUNIT_PARAMS_OSS="${OUT_DIR}/junit_params_opensource.xml"
JUNIT_PARAMS_KIMI="${OUT_DIR}/junit_params_kimi.xml"
TOOL_JSON_REPORT="${OUT_DIR}/tool_call_schema_report.json"

mkdir -p "${OUT_DIR}"

echo "=== Kimi Vendor Verifier - Jenkins Run ===" | tee -a "${LOG_FILE}"
echo "Suite     : ${KVV_SUITE}"       | tee -a "${LOG_FILE}"
echo "Base URL  : ${KIMI_BASE_URL}"   | tee -a "${LOG_FILE}"
echo "Model     : ${MODEL_NAME}"       | tee -a "${LOG_FILE}"
echo "ThinkMode : ${KVV_THINK_MODE}"   | tee -a "${LOG_FILE}"
echo "Thinking  : ${KVV_THINKING}"      | tee -a "${LOG_FILE}"
echo "OutDir    : ${OUT_DIR}"          | tee -a "${LOG_FILE}"
echo "Log       : ${LOG_FILE}"         | tee -a "${LOG_FILE}"

set +e
HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
    -H "Authorization: Bearer ${KIMI_API_KEY}" \
    "${KIMI_BASE_URL}/models" 2>/dev/null)"
set -e
echo "Connectivity(/models): HTTP ${HTTP_CODE:-000}" | tee -a "${LOG_FILE}"

OVERALL_STATUS=0

run_suite() {
    local junit_xml="$1"
    shift
    set +e
    uv run --no-sync pytest "$@" \
        --base-url "${KIMI_BASE_URL}" \
        --api-key "${KIMI_API_KEY}" \
        --smoke-model "${MODEL_NAME}" \
        -n "${KVV_PARALLEL_JOBS}" \
        --reruns "${KVV_RERUNS}" \
        --reruns-delay "${KVV_RERUNS_DELAY}" \
        --junitxml="${junit_xml}" \
        -ra -v 2>&1 | tee -a "${LOG_FILE}"
    local rc
    rc="${PIPESTATUS[0]}"
    set -e
    if [ "${rc}" -ne 0 ] && [ "${OVERALL_STATUS}" -eq 0 ]; then
        OVERALL_STATUS="${rc}"
    fi
}

THINKING_ARGS=()
if [ "${KVV_THINKING}" = "true" ]; then
    THINKING_ARGS=(--thinking)
fi

TOOL_ARGS=(--case-dir "${KVV_CASE_DIR}" --selection "${KVV_SELECTION}" --max-tokens "${KVV_MAX_TOKENS}")
if [ -n "${KVV_MAX_CASES}" ]; then
    TOOL_ARGS+=(--max-cases "${KVV_MAX_CASES}")
fi

# ---------- params 套件：opensource + kimi 两种模式（与本地脚本一致） ----------
if [ "${KVV_SUITE}" = "all" ] || [ "${KVV_SUITE}" = "params" ]; then
    echo "" | tee -a "${LOG_FILE}"
    echo ">>> [params/opensource] tests/params (think-mode=opensource)" | tee -a "${LOG_FILE}"
    echo "---------------------------------------------" | tee -a "${LOG_FILE}"
    run_suite "${JUNIT_PARAMS_OSS}" tests/params --think-mode opensource

    echo "" | tee -a "${LOG_FILE}"
    echo ">>> [params/kimi] tests/params (think-mode=kimi)" | tee -a "${LOG_FILE}"
    echo "---------------------------------------------" | tee -a "${LOG_FILE}"
    run_suite "${JUNIT_PARAMS_KIMI}" tests/params --think-mode kimi
fi

# ---------- 主套件 ----------
if [ "${KVV_SUITE}" = "all" ]; then
    echo "" | tee -a "${LOG_FILE}"
    echo ">>> [main] k3_features + prompt_tokens + tool_call_json_schema (think-mode=${KVV_THINK_MODE})" | tee -a "${LOG_FILE}"
    echo "---------------------------------------------" | tee -a "${LOG_FILE}"
    run_suite "${JUNIT_MAIN}" tests/k3_features tests/prompt_tokens tests/tool_call_json_schema \
        --think-mode "${KVV_THINK_MODE}" \
        "${THINKING_ARGS[@]}" \
        "${TOOL_ARGS[@]}" \
        --tool-json-report "${TOOL_JSON_REPORT}"
elif [ "${KVV_SUITE}" = "k3_features" ]; then
    echo "" | tee -a "${LOG_FILE}"
    echo ">>> [single] tests/k3_features (think-mode=${KVV_THINK_MODE})" | tee -a "${LOG_FILE}"
    echo "---------------------------------------------" | tee -a "${LOG_FILE}"
    run_suite "${JUNIT_MAIN}" tests/k3_features \
        --think-mode "${KVV_THINK_MODE}" \
        "${THINKING_ARGS[@]}"
elif [ "${KVV_SUITE}" = "prompt_tokens" ]; then
    echo "" | tee -a "${LOG_FILE}"
    echo ">>> [single] tests/prompt_tokens (think-mode=${KVV_THINK_MODE})" | tee -a "${LOG_FILE}"
    echo "---------------------------------------------" | tee -a "${LOG_FILE}"
    run_suite "${JUNIT_MAIN}" tests/prompt_tokens \
        --think-mode "${KVV_THINK_MODE}" \
        "${THINKING_ARGS[@]}"
elif [ "${KVV_SUITE}" = "tool_call_json_schema" ]; then
    echo "" | tee -a "${LOG_FILE}"
    echo ">>> [single] tests/tool_call_json_schema (think-mode=${KVV_THINK_MODE})" | tee -a "${LOG_FILE}"
    echo "---------------------------------------------" | tee -a "${LOG_FILE}"
    run_suite "${JUNIT_MAIN}" tests/tool_call_json_schema \
        --think-mode "${KVV_THINK_MODE}" \
        "${THINKING_ARGS[@]}" \
        "${TOOL_ARGS[@]}" \
        --tool-json-report "${TOOL_JSON_REPORT}"
fi

# ---------- 生成 HTML 汇总报告（与本地 generate_test_report.py 格式一致） ----------
REPORT_HTML="${OUT_DIR}/kkv_base_test_${TIMESTAMP}_report.html"
set +e
uv run --no-sync python generate_test_report.py \
    --out-dir "${OUT_DIR}" \
    --model "${MODEL_NAME}" \
    --base-url "${KIMI_BASE_URL}" \
    --timestamp "${TIMESTAMP}" \
    --output "${REPORT_HTML}" 2>&1 | tee -a "${LOG_FILE}"
set -e

# ---------- 生成 stats.properties（Jenkins 邮件统计用） ----------
set +e
uv run --no-sync python - "${OUT_DIR}" <<'PYEOF' 2>&1 | tee -a "${LOG_FILE}"
import sys
from pathlib import Path

from generate_test_report import JUNIT_FILES, RUN_LABELS, parse_junit

out_dir = Path(sys.argv[1])
runs = []
for name in JUNIT_FILES:
    path = out_dir / name
    if path.is_file():
        runs.append(parse_junit(path, RUN_LABELS.get(name, name)))
if not runs:
    print("error: no junit xml found in %s" % out_dir)
    sys.exit(1)

total = sum(r.total for r in runs)
passed = sum(r.passed for r in runs)
failed = sum(r.failed for r in runs)
skipped = sum(r.skipped for r in runs)
errors = sum(r.errors for r in runs)
rate = passed / total * 100 if total else 0.0
result = "PASS" if failed == 0 and errors == 0 else "FAIL"

lines = [
    "total=%d" % total,
    "passed=%d" % passed,
    "failed=%d" % failed,
    "skipped=%d" % skipped,
    "errors=%d" % errors,
    "rate=%.2f" % rate,
    "result=%s" % result,
]
(out_dir / "stats.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("stats.properties: " + " ".join(lines))
PYEOF
set -e

echo "=============================================" | tee -a "${LOG_FILE}"
if [ -s "${REPORT_HTML}" ]; then
    echo "Report : ${REPORT_HTML}" | tee -a "${LOG_FILE}"
else
    echo "Warning: HTML summary report was not generated" | tee -a "${LOG_FILE}"
fi
echo "=============================================" | tee -a "${LOG_FILE}"

exit "${OVERALL_STATUS}"

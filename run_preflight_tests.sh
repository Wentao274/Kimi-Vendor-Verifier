#!/usr/bin/env bash
set -euo pipefail

# ==================== SGLang 部署配置 ====================
# SGLang 服务地址，注意以 /v1 结尾
export KIMI_BASE_URL="http://10.11.75.45:3000/v1"
# SGLang 默认不校验 API Key，填任意非空占位值即可；
# 若启动时指定了 --api-key，则填真实值
export KIMI_API_KEY="EMPTY"
# SGLang 实际服务的模型名，即 /v1/models 返回的 id
export MODEL_NAME="kimi-k3"

# 主套件（k3_features / prompt_tokens / tool_call_json_schema）的 thinking 参数格式
THINK_MODE="opensource"

# 并发与重试策略（遇到 429 限流时可将 PARALLEL_JOBS 降为 2 或 1）
PARALLEL_JOBS=4
RERUNS=3
RERUNS_DELAY=2
# =======================================================

TIMESTAMP="$(date +%Y%m%d%H%M%S)"
MODEL_NAME_SAFE="${MODEL_NAME//\//_}"
OUT_DIR="outputs/${MODEL_NAME_SAFE}_${TIMESTAMP}"
LOG_FILE="${OUT_DIR}/kkv_base_test_${TIMESTAMP}.log"
# 各轮 pytest 仅输出 junit XML 到本目录；测试结束后由 generate_test_report.py
# 将目录下的全部 XML 汇总转换为一份 HTML 报告，同样落在本目录（不依赖 pytest-testreport 插件）
JUNIT_MAIN="${OUT_DIR}/junit_main.xml"
JUNIT_PARAMS_OSS="${OUT_DIR}/junit_params_opensource.xml"
JUNIT_PARAMS_KIMI="${OUT_DIR}/junit_params_kimi.xml"
TOOL_JSON_REPORT="${OUT_DIR}/tool_call_schema_report.json"

mkdir -p "${OUT_DIR}"

echo "=== Kimi Vendor Verifier - SGLang Deployment ===" | tee -a "${LOG_FILE}"
echo "Base URL  : ${KIMI_BASE_URL}"                    | tee -a "${LOG_FILE}"
echo "Model     : ${MODEL_NAME}"                       | tee -a "${LOG_FILE}"
echo "Run 1/3   : tests/params (think-mode=opensource)" | tee -a "${LOG_FILE}"
echo "Run 2/3   : tests/params (think-mode=kimi)"      | tee -a "${LOG_FILE}"
echo "Run 3/3   : k3_features + prompt_tokens + tool_call_json_schema (${THINK_MODE})" | tee -a "${LOG_FILE}"
echo "Log       : ${LOG_FILE}"                         | tee -a "${LOG_FILE}"
echo "============================================="     | tee -a "${LOG_FILE}"

OVERALL_STATUS=0

# ---------- [1/3] params 套件：opensource 模式 ----------
echo ""                                                   | tee -a "${LOG_FILE}"
echo ">>> [1/3] tests/params (think-mode=opensource)"   | tee -a "${LOG_FILE}"
echo "---------------------------------------------"     | tee -a "${LOG_FILE}"
set +e
uv run --no-sync pytest tests/params \
    --base-url "${KIMI_BASE_URL}" \
    --api-key "${KIMI_API_KEY}" \
    --smoke-model "${MODEL_NAME}" \
    --think-mode opensource \
    -n "${PARALLEL_JOBS}" \
    --reruns "${RERUNS}" \
    --reruns-delay "${RERUNS_DELAY}" \
    --junitxml="${JUNIT_PARAMS_OSS}" \
    -ra -v 2>&1 | tee -a "${LOG_FILE}"
STATUS_PARAMS_OSS=${PIPESTATUS[0]}
set -e

# ---------- [2/3] params 套件：kimi 模式 ----------
echo ""                                                   | tee -a "${LOG_FILE}"
echo ">>> [2/3] tests/params (think-mode=kimi)"         | tee -a "${LOG_FILE}"
echo "---------------------------------------------"     | tee -a "${LOG_FILE}"
set +e
uv run --no-sync pytest tests/params \
    --base-url "${KIMI_BASE_URL}" \
    --api-key "${KIMI_API_KEY}" \
    --smoke-model "${MODEL_NAME}" \
    --think-mode kimi \
    -n "${PARALLEL_JOBS}" \
    --reruns "${RERUNS}" \
    --reruns-delay "${RERUNS_DELAY}" \
    --junitxml="${JUNIT_PARAMS_KIMI}" \
    -ra -v 2>&1 | tee -a "${LOG_FILE}"
STATUS_PARAMS_KIMI=${PIPESTATUS[0]}
set -e

# ---------- [3/3] 其余套件：THINK_MODE 模式 ----------
echo ""                                                   | tee -a "${LOG_FILE}"
echo ">>> [3/3] k3_features + prompt_tokens + tool_call_json_schema (think-mode=${THINK_MODE})" | tee -a "${LOG_FILE}"
echo "---------------------------------------------"     | tee -a "${LOG_FILE}"
set +e
uv run --no-sync pytest tests/k3_features tests/prompt_tokens tests/tool_call_json_schema \
    --base-url "${KIMI_BASE_URL}" \
    --api-key "${KIMI_API_KEY}" \
    --smoke-model "${MODEL_NAME}" \
    --think-mode "${THINK_MODE}" \
    --thinking \
    -n "${PARALLEL_JOBS}" \
    --reruns "${RERUNS}" \
    --reruns-delay "${RERUNS_DELAY}" \
    --junitxml="${JUNIT_MAIN}" \
    --tool-json-report="${TOOL_JSON_REPORT}" \
    -ra -v 2>&1 | tee -a "${LOG_FILE}"
STATUS_MAIN=${PIPESTATUS[0]}
set -e

# ---------- 汇总 ----------
echo "=============================================" | tee -a "${LOG_FILE}"
echo "params (opensource) : $([ "${STATUS_PARAMS_OSS}" -eq 0 ] && echo PASS || echo "FAIL (exit ${STATUS_PARAMS_OSS})")" | tee -a "${LOG_FILE}"
echo "params (kimi)       : $([ "${STATUS_PARAMS_KIMI}" -eq 0 ] && echo PASS || echo "FAIL (exit ${STATUS_PARAMS_KIMI})")" | tee -a "${LOG_FILE}"
echo "main suites         : $([ "${STATUS_MAIN}" -eq 0 ] && echo PASS || echo "FAIL (exit ${STATUS_MAIN})")" | tee -a "${LOG_FILE}"

if [ "${STATUS_PARAMS_OSS}" -ne 0 ] && [ "${OVERALL_STATUS}" -eq 0 ]; then OVERALL_STATUS=${STATUS_PARAMS_OSS}; fi
if [ "${STATUS_PARAMS_KIMI}" -ne 0 ] && [ "${OVERALL_STATUS}" -eq 0 ]; then OVERALL_STATUS=${STATUS_PARAMS_KIMI}; fi
if [ "${STATUS_MAIN}" -ne 0 ] && [ "${OVERALL_STATUS}" -eq 0 ]; then OVERALL_STATUS=${STATUS_MAIN}; fi

# ---------- 生成 HTML 汇总报告 ----------
REPORT_HTML="${OUT_DIR}/kkv_base_test_${TIMESTAMP}_report.html"
set +e
uv run --no-sync python generate_test_report.py \
    --out-dir "${OUT_DIR}" \
    --model "${MODEL_NAME}" \
    --base-url "${KIMI_BASE_URL}" \
    --timestamp "${TIMESTAMP}" \
    --output "${REPORT_HTML}" 2>&1 | tee -a "${LOG_FILE}"
set -e
if [ -s "${REPORT_HTML}" ]; then
    echo "Report    : ${REPORT_HTML}" | tee -a "${LOG_FILE}"
else
    echo "Warning   : HTML summary report was not generated" | tee -a "${LOG_FILE}"
fi

echo "=============================================" | tee -a "${LOG_FILE}"
if [ "${OVERALL_STATUS}" -eq 0 ]; then
    echo "Result: ALL TESTS PASSED" | tee -a "${LOG_FILE}"
else
    echo "Result: FAILED (exit code ${OVERALL_STATUS})" | tee -a "${LOG_FILE}"
fi

exit "${OVERALL_STATUS}"

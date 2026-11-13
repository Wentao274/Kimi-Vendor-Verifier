#!/usr/bin/env bash
set -euo pipefail

# ==================== Jenkins 触发的 KVV 基准评测入口 ====================
# 与 KVV 功能验证(run_kimivv_tests.sh)相互独立：
#   - 本脚本执行 inspect-ai 基准评测(ocrbench / mmmu / kimi_toolcall)，
#     经 kvv_bench_runner.py 运行(补齐 --limit 与机读 summary)，
#     每个评测任务×思考强度组合产出独立 summary.json
#
# 环境变量（模型基础信息由 JenkinsfileBench 参数透传）：
#   KIMI_BASE_URL     API 地址（以 /v1 结尾；JenkinsfileBench 传入拼接好的 BASE_URL_V1）
#   KIMI_API_KEY      API Key（SGLang 未开启鉴权时填 EMPTY）
#   MODEL_NAME        模型名，即 /v1/models 返回的 id
#   KVB_BENCH         ocrbench | mmmu | kimi_toolcall（必填）
#   KVB_EFFORT        none | low | high | max（空 = 传统 --thinking 模式）
#   KVB_THINK_MODE    thinking 参数格式：kimi | opensource（默认 kimi）
#   KVB_THINKING      KVB_EFFORT 为空时是否开启 --thinking（默认 true）
#   KVB_LIMIT         样本数上限（空 = 全量；MMMU 全量约 1.17 万样本，CI 不建议）
#   KVB_MAX_TOKENS    单次响应最大 tokens（按 README 推荐值）
#   KVB_TEMPERATURE   采样温度（默认 1.0）
#   KVB_TOP_P         top_p（默认 0.95）
#   KVB_EPOCHS        采样轮数（默认 1）
#   KVB_CONNECTIONS   并发连接数（默认 8；官方 API 为 50，SGLang 自部署请勿照抄）
#   KVB_TIMEOUT       单请求超时秒数（默认 86400）
#   KVB_STREAM        是否流式请求（默认 true，长生成保活）
#   KVB_DATASET       kimi_toolcall 数据集路径（默认仓库内 LFS 文件）
#   KVB_HTTP_PROXY    代理地址（仅数据集下载阶段生效；评测阶段由 runner 剥离代理；空 = 直连）
#   KVB_HF_ENDPOINT   HuggingFace 镜像端点（可选，直连不可达时可设镜像）
#   KVB_OUT_DIR       评测结果根目录（必填；组合目录 <KVB_OUT_DIR>/<bench>/<effort>/）
# =======================================================

cd "$(dirname "$0")"

# ---------- 确保在 uv 虚拟环境中执行 ----------
# 已激活(VIRTUAL_ENV 已设置)则跳过;未激活但仓库根目录存在 .venv 则自动 source;
# 完全没有虚拟环境则报错退出,避免 uv run 解析到错误的环境
if [ -z "${VIRTUAL_ENV:-}" ]; then
    if [ -f ".venv/bin/activate" ]; then
        # shellcheck disable=SC1091
        source .venv/bin/activate
        echo "Activated uv venv: ${VIRTUAL_ENV}"
    else
        echo "ERROR: no active venv and .venv not found in $(pwd)" >&2
        echo "       create it first: uv venv && uv pip install -r pyproject.toml" >&2
        exit 1
    fi
else
    echo "uv venv already active: ${VIRTUAL_ENV}"
fi

: "${KVB_OUT_DIR:?KVB_OUT_DIR is required}"
KVB_BENCH="${KVB_BENCH:-ocrbench}"
KVB_EFFORT="${KVB_EFFORT:-}"
KVB_THINK_MODE="${KVB_THINK_MODE:-kimi}"
KVB_THINKING="${KVB_THINKING:-true}"
KVB_LIMIT="${KVB_LIMIT:-}"
KVB_MAX_TOKENS="${KVB_MAX_TOKENS:-16384}"
KVB_TEMPERATURE="${KVB_TEMPERATURE:-1.0}"
KVB_TOP_P="${KVB_TOP_P:-0.95}"
KVB_EPOCHS="${KVB_EPOCHS:-1}"
KVB_CONNECTIONS="${KVB_CONNECTIONS:-8}"
KVB_TIMEOUT="${KVB_TIMEOUT:-86400}"
KVB_STREAM="${KVB_STREAM:-true}"
KVB_DATASET="${KVB_DATASET:-toolcall_benchmark/toolcall_thinking_samples.stable2.jsonl}"
KVB_HTTP_PROXY="${KVB_HTTP_PROXY:-}"
KVB_HF_ENDPOINT="${KVB_HF_ENDPOINT:-}"

EFFORT_LABEL="${KVB_EFFORT:-legacy}"
RUN_DIR="${KVB_OUT_DIR}/${KVB_BENCH}/${EFFORT_LABEL}"
LOG_FILE="${RUN_DIR}/bench_${KVB_BENCH}_${EFFORT_LABEL}.log"
mkdir -p "${RUN_DIR}"

# 代理仅用于 HuggingFace 数据集下载阶段(进程级 export,覆盖 kvv_bench_runner.py
# 的数据集加载;已缓存的数据集不会重复下载)。数据集就绪后 kvv_bench_runner.py
# 会在发起任何模型请求前剥离代理环境变量,评测全程直连。
if [ -n "${KVB_HTTP_PROXY}" ]; then
    export https_proxy="${KVB_HTTP_PROXY}"
    export http_proxy="${KVB_HTTP_PROXY}"
    echo "HTTP proxy enabled for dataset download: ${KVB_HTTP_PROXY}"
fi
if [ -n "${KVB_HF_ENDPOINT}" ]; then
    export HF_ENDPOINT="${KVB_HF_ENDPOINT}"
    echo "HF_ENDPOINT: ${KVB_HF_ENDPOINT}"
fi

ARGS=(
    --bench "${KVB_BENCH}"
    --model "${MODEL_NAME}"
    --think-mode "${KVB_THINK_MODE}"
    --max-tokens "${KVB_MAX_TOKENS}"
    --temperature "${KVB_TEMPERATURE}"
    --top-p "${KVB_TOP_P}"
    --epochs "${KVB_EPOCHS}"
    --max-connections "${KVB_CONNECTIONS}"
    --client-timeout "${KVB_TIMEOUT}"
    --out-dir "${RUN_DIR}"
)
if [ -n "${KVB_EFFORT}" ]; then
    ARGS+=(--effort "${KVB_EFFORT}")
else
    if [ "${KVB_THINKING}" = "true" ]; then
        ARGS+=(--thinking)
    fi
fi
if [ -n "${KVB_LIMIT}" ]; then
    ARGS+=(--limit "${KVB_LIMIT}")
fi
if [ "${KVB_STREAM}" = "true" ]; then
    ARGS+=(--stream)
fi
if [ -n "${KVB_DATASET}" ]; then
    ARGS+=(--dataset "${KVB_DATASET}")
fi

echo "=== Kimi Vendor Verifier - Benchmark ===" | tee -a "${LOG_FILE}"
echo "Bench     : ${KVB_BENCH} (effort=${EFFORT_LABEL})"  | tee -a "${LOG_FILE}"
echo "Base URL  : ${KIMI_BASE_URL}"                      | tee -a "${LOG_FILE}"
echo "Model     : ${MODEL_NAME}"                          | tee -a "${LOG_FILE}"
echo "Limit     : ${KVB_LIMIT:-FULL}"                     | tee -a "${LOG_FILE}"
echo "MaxTokens : ${KVB_MAX_TOKENS} | Conn: ${KVB_CONNECTIONS} | Epochs: ${KVB_EPOCHS}" | tee -a "${LOG_FILE}"
echo "RunDir    : ${RUN_DIR}"                            | tee -a "${LOG_FILE}"
echo "============================================="       | tee -a "${LOG_FILE}"

set +e
uv run --no-sync python kvv_bench_runner.py "${ARGS[@]}" 2>&1 | tee -a "${LOG_FILE}"
STATUS=${PIPESTATUS[0]}
set -e

if [ -n "${KVB_HTTP_PROXY}" ]; then
    unset https_proxy http_proxy 2>/dev/null || true
fi
if [ -n "${KVB_HF_ENDPOINT}" ]; then
    unset HF_ENDPOINT 2>/dev/null || true
fi

if [ "${STATUS}" -eq 0 ]; then
    echo "Result: BENCH RUN PASSED (${KVB_BENCH}/${EFFORT_LABEL})" | tee -a "${LOG_FILE}"
else
    echo "Result: BENCH RUN FAILED (exit ${STATUS}, ${KVB_BENCH}/${EFFORT_LABEL})" | tee -a "${LOG_FILE}"
fi
exit "${STATUS}"

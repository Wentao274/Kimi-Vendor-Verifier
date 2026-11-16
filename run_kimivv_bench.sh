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
#   --- 以下仅 KVB_BENCH=beam（BEAM 1M 长程记忆两阶段评测）时使用 ---
#   KVB_BEAM_DATA         数据规格：1m（35 对话×20 题=700 题）| 128k（128k 对话变体）（默认 1m）
#   KVB_BEAM_PLAIN_TEXT   true=plain-text 单消息拼接（与上游 CI 默认一致）| false=多轮 messages（默认 true）
#   KVB_BEAM_CONCURRENCY  生成阶段并发请求数（默认 8；1M prompt 较重，SGLang 自部署勿照抄官方 50）
#   KVB_BEAM_TIMEOUT      生成阶段单请求超时秒数（默认 3600；1M prompt + 长思考生成较慢）
#   KVB_BEAM_DRY_RUN      true=冒烟：仅构建 prompt 不发请求，跳过判分与汇总（默认 false）
#   KVB_JUDGE_BASE_URL    判分端点（带 /v1；空=仅生成答案跳过判分；须与被测端点独立、可直连）
#   KVB_JUDGE_API_KEY     判分 API Key（KVB_JUDGE_BASE_URL 非空时必填）
#   KVB_JUDGE_MODEL       判分模型（默认 kimi-k2.6，与上游 CI 一致）
#   KVB_JUDGE_TEMPERATURE 判分采样温度（默认 1.0；kimi 系判分模型须锁定 1.0，其他模型可 0.3）
#   KVB_JUDGE_CONCURRENCY 判分并发请求数（默认 10，与上游 CI 一致）
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
KVB_BEAM_DATA="${KVB_BEAM_DATA:-1m}"
KVB_BEAM_PLAIN_TEXT="${KVB_BEAM_PLAIN_TEXT:-true}"
KVB_BEAM_CONCURRENCY="${KVB_BEAM_CONCURRENCY:-8}"
KVB_BEAM_TIMEOUT="${KVB_BEAM_TIMEOUT:-3600}"
KVB_BEAM_DRY_RUN="${KVB_BEAM_DRY_RUN:-false}"
KVB_JUDGE_BASE_URL="${KVB_JUDGE_BASE_URL:-}"
KVB_JUDGE_API_KEY="${KVB_JUDGE_API_KEY:-}"
KVB_JUDGE_MODEL="${KVB_JUDGE_MODEL:-kimi-k2.6}"
KVB_JUDGE_TEMPERATURE="${KVB_JUDGE_TEMPERATURE:-1.0}"
KVB_JUDGE_CONCURRENCY="${KVB_JUDGE_CONCURRENCY:-10}"

EFFORT_LABEL="${KVB_EFFORT:-legacy}"
RUN_DIR="${KVB_OUT_DIR}/${KVB_BENCH}/${EFFORT_LABEL}"
LOG_FILE="${RUN_DIR}/bench_${KVB_BENCH}_${EFFORT_LABEL}.log"
mkdir -p "${RUN_DIR}"

# ---------- BEAM(1M) 两阶段评测分支 ----------
# KVB_BENCH=beam 时不走 inspect-ai：先 beam_generate.py 生成 answers（断点续跑），
# 再 beam_judge.py LLM-as-Judge 判分（断点续跑），最后 kvv_bench_runner.py
# beam-summary 产出与其他基准同构的 summary.json（汇总与邮件自动包含）。
# 分支内所有路径均显式 exit，不会落入下方通用 inspect-ai 流程。
if [ "${KVB_BENCH}" = "beam" ]; then
    case "${KVB_BEAM_DATA}" in
        1m|128k) ;;
        *) echo "ERROR: KVB_BEAM_DATA 须为 1m 或 128k（当前: ${KVB_BEAM_DATA}）" >&2; exit 1 ;;
    esac
    CHATS_ARG="data/beam_${KVB_BEAM_DATA}_chats.jsonl.gz"
    QUESTIONS_ARG="data/beam_${KVB_BEAM_DATA}_questions.jsonl"
    if [ ! -f "beam/${CHATS_ARG}" ] || [ ! -f "beam/${QUESTIONS_ARG}" ]; then
        echo "ERROR: BEAM 数据缺失: beam/${CHATS_ARG} / beam/${QUESTIONS_ARG}（先 git lfs pull）" >&2
        exit 1
    fi

    # BEAM 数据随仓库 LFS 分发，无下载阶段；模型/判分请求必须直连，
    # 与 kvv_bench_runner.py 评测阶段的做法一致，直接剥离所有代理变量
    unset https_proxy http_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY 2>/dev/null || true

    MODE="multiturn"
    if [ "${KVB_BEAM_PLAIN_TEXT}" = "true" ]; then
        MODE="plain"
    fi
    ANSWERS_FILE="${RUN_DIR}/answers_${MODE}.jsonl"
    SCORES_FILE="${RUN_DIR}/scores_${MODE}.jsonl"

    # thinking extra_body 与 kvv_bench_runner run 完全同源（effort 优先 K3，
    # KVB_EFFORT 为空时回退 legacy KVB_THINKING），透传给 beam_generate
    THINKING_JSON="$(uv run --no-sync python -c '
import json, sys
from eval import get_thinking_extra_body
print(json.dumps(get_thinking_extra_body(sys.argv[2] == "true", sys.argv[3], sys.argv[1] or None)))
' "${KVB_EFFORT}" "${KVB_THINKING}" "${KVB_THINK_MODE}")" || {
        echo "ERROR: thinking extra_body 计算失败" >&2
        exit 1
    }

    echo "=== Kimi Vendor Verifier - BEAM (${KVB_BEAM_DATA}) ===" | tee -a "${LOG_FILE}"
    echo "Bench     : beam (effort=${EFFORT_LABEL}, prompt-mode=${MODE}, dry-run=${KVB_BEAM_DRY_RUN})" | tee -a "${LOG_FILE}"
    echo "Base URL  : ${KIMI_BASE_URL}" | tee -a "${LOG_FILE}"
    echo "Model     : ${MODEL_NAME}" | tee -a "${LOG_FILE}"
    echo "Data      : ${CHATS_ARG} + ${QUESTIONS_ARG}" | tee -a "${LOG_FILE}"
    echo "Limit     : ${KVB_LIMIT:-FULL}" | tee -a "${LOG_FILE}"
    echo "MaxTokens : ${KVB_MAX_TOKENS} | Conc: ${KVB_BEAM_CONCURRENCY} | Timeout: ${KVB_BEAM_TIMEOUT}s | Temp: ${KVB_TEMPERATURE} / TopP: ${KVB_TOP_P}" | tee -a "${LOG_FILE}"
    echo "Thinking  : ${THINKING_JSON}" | tee -a "${LOG_FILE}"
    echo "Judge     : ${KVB_JUDGE_BASE_URL:-（未配置，仅生成答案）} | model=${KVB_JUDGE_MODEL} | temp=${KVB_JUDGE_TEMPERATURE} | conc=${KVB_JUDGE_CONCURRENCY}" | tee -a "${LOG_FILE}"
    echo "RunDir    : ${RUN_DIR}" | tee -a "${LOG_FILE}"
    echo "=============================================" | tee -a "${LOG_FILE}"

    GEN_ARGS=(
        --model "${MODEL_NAME}"
        --base-url "${KIMI_BASE_URL}"
        --api-key "${KIMI_API_KEY}"
        --chats-file "${CHATS_ARG}"
        --questions-file "${QUESTIONS_ARG}"
        --concurrency "${KVB_BEAM_CONCURRENCY}"
        --timeout "${KVB_BEAM_TIMEOUT}"
        --max-tokens "${KVB_MAX_TOKENS}"
        --temperature "${KVB_TEMPERATURE}"
        --top-p "${KVB_TOP_P}"
        --output "${ANSWERS_FILE}"
    )
    if [ -n "${KVB_LIMIT}" ]; then
        GEN_ARGS+=(--limit "${KVB_LIMIT}")
    fi
    if [ "${KVB_BEAM_PLAIN_TEXT}" = "true" ]; then
        GEN_ARGS+=(--plain-text)
    fi
    if [ -n "${THINKING_JSON}" ] && [ "${THINKING_JSON}" != "{}" ]; then
        GEN_ARGS+=(--thinking-json "${THINKING_JSON}")
    fi
    if [ "${KVB_BEAM_DRY_RUN}" = "true" ]; then
        GEN_ARGS+=(--dry-run)
    fi

    GEN_START="$(date +%s)"
    set +e
    ( cd beam && uv run --no-sync python beam_generate.py "${GEN_ARGS[@]}" ) 2>&1 | tee -a "${LOG_FILE}"
    GEN_RC="${PIPESTATUS[0]}"
    set -e
    GEN_DUR="$(( $(date +%s) - GEN_START ))"

    if [ "${KVB_BEAM_DRY_RUN}" = "true" ]; then
        echo "Result: BEAM DRY-RUN DONE（未发送请求，跳过判分与汇总，beam/${EFFORT_LABEL}）" | tee -a "${LOG_FILE}"
        exit 0
    fi
    if [ "${GEN_RC}" -ne 0 ]; then
        echo "Result: BENCH RUN FAILED (generate exit ${GEN_RC}, beam/${EFFORT_LABEL})" | tee -a "${LOG_FILE}"
        exit "${GEN_RC}"
    fi
    if [ ! -s "${ANSWERS_FILE}" ]; then
        echo "Result: BENCH RUN FAILED (answers 为空: ${ANSWERS_FILE}, beam/${EFFORT_LABEL})" | tee -a "${LOG_FILE}"
        exit 1
    fi

    if [ -z "${KVB_JUDGE_BASE_URL}" ]; then
        echo "WARN: KVB_JUDGE_BASE_URL 未配置，跳过判分与汇总；answers 已生成: ${ANSWERS_FILE}" | tee -a "${LOG_FILE}"
        echo "Result: BENCH RUN PASSED (generate-only, beam/${EFFORT_LABEL})" | tee -a "${LOG_FILE}"
        exit 0
    fi
    if [ -z "${KVB_JUDGE_API_KEY}" ]; then
        echo "ERROR: 已配置 KVB_JUDGE_BASE_URL 但缺少 KVB_JUDGE_API_KEY (beam/${EFFORT_LABEL})" | tee -a "${LOG_FILE}" >&2
        exit 1
    fi

    JUDGE_ARGS=(
        --answers "${ANSWERS_FILE}"
        --output "${SCORES_FILE}"
        --judge-model "${KVB_JUDGE_MODEL}"
        --judge-base-url "${KVB_JUDGE_BASE_URL}"
        --judge-api-key "${KVB_JUDGE_API_KEY}"
        --judge-temperature "${KVB_JUDGE_TEMPERATURE}"
        --concurrency "${KVB_JUDGE_CONCURRENCY}"
    )
    JUDGE_START="$(date +%s)"
    set +e
    ( cd beam && uv run --no-sync python beam_judge.py "${JUDGE_ARGS[@]}" ) 2>&1 | tee -a "${LOG_FILE}"
    JUDGE_RC="${PIPESTATUS[0]}"
    set -e
    JUDGE_DUR="$(( $(date +%s) - JUDGE_START ))"
    if [ "${JUDGE_RC}" -ne 0 ] || [ ! -s "${SCORES_FILE}" ]; then
        echo "Result: BENCH RUN FAILED (judge exit ${JUDGE_RC}, beam/${EFFORT_LABEL})" | tee -a "${LOG_FILE}"
        exit 1
    fi

    SUM_ARGS=(
        --answers "${ANSWERS_FILE}"
        --scores "${SCORES_FILE}"
        --out-dir "${RUN_DIR}"
        --effort "${EFFORT_LABEL}"
        --model "${MODEL_NAME}"
        --think-mode "${KVB_THINK_MODE}"
        --beam-data "${KVB_BEAM_DATA}"
        --temperature "${KVB_TEMPERATURE}"
        --max-tokens "${KVB_MAX_TOKENS}"
        --gen-duration "${GEN_DUR}"
        --judge-duration "${JUDGE_DUR}"
    )
    if [ "${MODE}" = "plain" ]; then
        SUM_ARGS+=(--plain-text)
    fi
    if [ -n "${KVB_LIMIT}" ]; then
        SUM_ARGS+=(--limit "${KVB_LIMIT}")
    fi
    set +e
    uv run --no-sync python kvv_bench_runner.py beam-summary "${SUM_ARGS[@]}" 2>&1 | tee -a "${LOG_FILE}"
    SUM_RC="${PIPESTATUS[0]}"
    set -e
    if [ "${SUM_RC}" -ne 0 ]; then
        echo "Result: BENCH RUN FAILED (beam-summary exit ${SUM_RC}, beam/${EFFORT_LABEL})" | tee -a "${LOG_FILE}"
        exit "${SUM_RC}"
    fi
    echo "Result: BENCH RUN PASSED (beam/${EFFORT_LABEL})" | tee -a "${LOG_FILE}"
    exit 0
fi

# ---------- 通用 inspect-ai 评测流程（ocrbench / mmmu / kimi_toolcall） ----------

# 代理仅用于 HuggingFace 数据集下载阶段(进程级 export,覆盖 kvv_bench_runner.py
# 的数据集加载;已缓存的数据集不会重复下载)。数据集就绪后 kvv_bench_runner.py
# 会在发起任何模型请求前剥离代理环境变量,评测全程直连。
if [ -n "${KVB_HTTP_PROXY}" ]; then
    case "${KVB_HTTP_PROXY}" in
        *'<'*)
            echo "KVB_HTTP_PROXY 是占位符(${KVB_HTTP_PROXY}),按直连处理;如需代理请填写真实地址"
            KVB_HTTP_PROXY=""
            ;;
    esac
fi
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
uv run --no-sync python kvv_bench_runner.py run "${ARGS[@]}" 2>&1 | tee -a "${LOG_FILE}"
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

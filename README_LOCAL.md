# Kimi Vendor Verifier 本地 CI/CD 使用说明

本文件补足 [`README.md`](README.md) 中**未覆盖**的本地脚本编排、Jenkins 流水线
与远程执行约定。`README.md` 讲的是单条 pytest 命令与各套件的断言细节;本文件
讲的是**如何用两个入口脚本一键跑全部套件、如何在 Jenkins 上参数化触发、远程
ssh 到测试主机执行、把报告/邮件发回**。

---

## 1. 文件清单

| 文件 | 角色 | 说明 |
|------|------|------|
| `run_preflight_tests.sh` | 本地手工执行入口 | 配置固定写在脚本顶部,改完直接跑,适合开发者自测 |
| `run_kimivv_tests.sh` | Jenkins 触发入口 | 配置全部通过环境变量注入,按 `KVV_SUITE` 选择套件 |
| `Jenkinsfile` | Jenkins 声明式流水线 | 参数化触发、远程执行、报告拉取、邮件通知 |
| `generate_test_report.py` | HTML 汇总报告生成器 | 两个入口脚本共用,读 junit XML 出 HTML 报告 |
| `run_kimivv_bench.sh` | Jenkins 基准评测入口 | 执行单个(评测任务×思考强度)组合,配置全通过 `KVB_*` 环境变量注入 |
| `kvv_bench_runner.py` | 基准评测运行器 | 包装 inspect-ai 任务,补齐 `--limit` 与机读 `summary.json`,提供 `aggregate` 汇总模式 |
| `generate_bench_report.py` | 基准评测 HTML 报告生成器 | 读 `bench/<bench>/<effort>/summary.json` 出 HTML 报告(aggregate 后调用,作为邮件附件) |
| `JenkinsfileBench` | Jenkins 声明式流水线 | 基准评测(OCRBench/MMMU/ToolCall),与 `Jenkinsfile` 完全独立,见第 8 节 |

两个入口脚本**相互独立、互不影响**:

- `run_preflight_tests.sh`:不读任何环境变量,所有配置(`KIMI_BASE_URL` /
  `MODEL_NAME` / `THINK_MODE` / 并发数等)写死在脚本顶部,改脚本即改配置;
- `run_kimivv_tests.sh`:不写死任何业务配置,全部从环境变量取值
  (带默认值兜底),由 Jenkinsfile 的 `运行测试` stage 统一注入。

两个脚本均已通过 git index 记录可执行位(`100755`),`git clone` 之后
直接 `./run_preflight_tests.sh` / `./run_kimivv_tests.sh` 即可执行。

---

## 2. 测试套件与 think-mode

### 2.1 五个测试套件

| 套件目录 | 验证内容 |
|----------|----------|
| `tests/params` | 参数约束:不可变参数(temperature/top_p/presence_penalty/frequency_penalty/n)默认值必须被接受、错误值必须被 400 拒绝 |
| `tests/k3_features` | K3 特性:动态工具、response_format、thinking effort、tool_choice、stop、tokenization groundtruth |
| `tests/prompt_tokens` | prompt token 计数与 groundtruth 比对(测试数据走 git-lfs) |
| `tests/tool_call_json_schema` | 工具调用 JSON Schema 校验(walle validator cases) |
| `tests/toolcall_reject_cases` | 非法 tool call 请求(缺 function name 等)必须被 400 拒绝 |

### 2.2 think-mode 两种格式

`--think-mode` 控制请求中 thinking 参数的**格式**(实现位于
`tests/helper.py`(params 等套件)与 `tests/tool_call_json_schema/validator.py`,
两处语义一致):

| think-mode | 请求 extra_body | 适用 |
|------------|-----------------|------|
| `opensource` | `{"chat_template_kwargs": {"thinking": true/false}}` | vLLM/SGLang 等开源部署 |
| `kimi` | `{"thinking": {"type": "enabled"/"disabled"}}` | Kimi 官方 API 格式 |

> 上游合并后 conftest 的 `--think-mode` choices 已收窄为
> `kimi | opensource`,不再支持 `none`。

### 2.3 params 套件必须双轮跑

`tests/params` 的目的是**验证参数本身**,因此无论 THINK_MODE 设成什么,
该套件固定按 `opensource` 和 `kimi` 两种格式各跑一轮(两个入口脚本行为
一致),分别产出独立的 junit XML。其余主套件按 `THINK_MODE` 选定的
格式发请求(新增的 `toolcall_reject_cases` 不受 THINK_MODE 影响,
其用例自带 thinking 参数或完全不传)。

> 注:上游已将 params 套件声明为 kimi-only(官方网关只认 kimi 格式);
> 本仓库面向 SGLang 自部署,双轮跑两种格式正是验证目标,故有意保留。

### 2.4 K3.1 (new_model) 用例标注

上游 k3.1 测试改造引入 5 个 (new_model) 功能模块
(`test_dynamic_tools` / `test_response_format` / `test_thinking_effort` /
`test_tokenization_groundtruth` / `test_tool_choice`,模块 docstring 首行均标注
"(new_model)")。这些模块**含** K3.1 新增功能用例,但并非全部用例都是 K3.1
专属——如 `test_response_format` 对 kimi-k3 即全部通过。模块内用例对任何
被测模型都会执行、不做跳过;其中 K3.1 专属用例若失败,对不含该能力的模型
(如 kimi-k3)属预期(能力缺失),而非部署缺陷。

`generate_test_report.py`(本仓库自建脚本,不改动上游代码)在报告层按模块清单
(`K31_MODULE_PREFIXES`)给相关套件/用例加 K3.1 徽标(套件行"含K3.1功能"、
明细用例行"K3.1功能"):

- 一、总览:新增 "含K3.1功能模块的用例" "通过(K3.1 范围内)" "失败(K3.1 范围内)"
  三行统计;存在 K3.1 范围用例且被测模型**不是** K3.1 时,追加
  "通过率(剔除含K3.1功能模块用例后)"——分子与分母同时剔除 5 个 (new_model)
  模块的全部用例(模块粒度,其中 K3.1 范围内通过的用例亦不再计入通过);
  表格下方以注解形式给出完整计算公式与 K3.1 范围的通过/失败/跳过分解;
  原总通过率行保留;并附徽标图例说明;
- 二、按套件统计:含 K3.1 功能的套件名后带 "含K3.1功能" 徽标(套件为聚合体,故用"含");
- 三、失败/错误/跳过明细:相关用例名后带 "K3.1功能" 徽标(个体用例,不用"含");
- 五、结论:单独一条列出 K3.1 范围内失败数及所属套件;非 K3.1 模型时说明
  属预期(能力缺失),K3.1 模型时说明需按功能实现/部署问题排查。

**被测模型即 K3.1(模型名含 `k3.1`,如 `kimi-k3.1`)时语义反转**:K3.1 功能
模块的失败不再是"能力缺失"预期——不显示 "剔除后通过率" 行;徽标悬停提示、
标注说明与结论均改为"并非能力缺失,需按功能实现/部署问题排查";K3.1 范围
统计行照常保留。按 `--model` 自动判定,无需额外参数。

> 注:上游若新增 (new_model) 模块,需同步扩充 `generate_test_report.py`
> 顶部的 `K31_MODULE_PREFIXES` 清单。邮件正文内嵌该 HTML 报告,徽标样式随之生效。

---

## 3. 本地执行:`run_preflight_tests.sh`

```bash
# 1. 按需修改脚本顶部配置块
#    KIMI_BASE_URL / KIMI_API_KEY / MODEL_NAME / THINK_MODE /
#    PARALLEL_JOBS / RERUNS / RERUNS_DELAY
./run_preflight_tests.sh
```

两个入口脚本(`run_preflight_tests.sh` / `run_kimivv_tests.sh`)启动时
都会先执行"虚拟环境守卫",确保所有测试命令运行在 uv 虚拟环境中:

- 已激活(`VIRTUAL_ENV` 已设置)→ 直接继续;
- 未激活但仓库根目录存在 `.venv/bin/activate` → 自动 `source` 激活
  (日志打印 `Activated uv venv: ...`);
- 连 `.venv` 都没有 → 立即报错退出(`exit 1`),并提示先执行
  `uv venv && uv pip install -r pyproject.toml`。

Jenkinsfile 的"运行测试" stage 同样会在执行脚本前
`source .venv/bin/activate`(见第 5 节 stage 详解)。

执行流程(固定 3 轮,与 Jenkins `SUITE=all` 完全等价):

| 轮次 | 命令要点 | junit XML |
|------|----------|-----------|
| 1/3 | `pytest tests/params --think-mode opensource` | `junit_params_opensource.xml` |
| 2/3 | `pytest tests/params --think-mode kimi` | `junit_params_kimi.xml` |
| 3/3 | `pytest tests/k3_features tests/prompt_tokens tests/tool_call_json_schema tests/toolcall_reject_cases --think-mode ${THINK_MODE} --thinking --tool-json-report ...` | `junit_main.xml` |

每轮均为 `uv run --no-sync pytest ... -n 4 --reruns 3` 并全程 `tee` 到日志;
退出码取第一个非零轮次(单轮失败不中断后续轮次,便于一次看全所有失败)。

产物目录 `outputs/<model>_<timestamp>/`:

```
outputs/kimi-k3_20261012123045/
├── kkv_base_test_20261012123045.log           # 全程总日志
├── junit_params_opensource.xml                 # params opensource 轮
├── junit_params_kimi.xml                       # params kimi 轮
├── junit_main.xml                              # 主套件轮
├── tool_call_schema_report.json                # tool 套件逐用例明细
└── kimi-k3_kvv_preflight_test_report_20261012123045.html    # HTML 汇总报告
```

HTML 报告由 `generate_test_report.py` 汇总目录下全部 junit XML 生成,
与 Jenkins 邮件中内嵌的报告完全同源。

---

## 4. Jenkins 触发:`run_kimivv_tests.sh`

`run_kimivv_tests.sh` 不直接读 Jenkins 参数,只认环境变量(均有默认值,
可脱离 Jenkins 单独手工调用):

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `KIMI_BASE_URL` | `http://127.0.0.1:3000/v1` | API 地址,以 `/v1` 结尾 |
| `KIMI_API_KEY` | `EMPTY` | 未鉴权时保持 EMPTY |
| `MODEL_NAME` | `kimi-k3` | `/v1/models` 返回的 id |
| `KVV_SUITE` | `all` | 套件选择,见下表 |
| `KVV_THINK_MODE` | `opensource` | 主套件 thinking 格式 |
| `KVV_THINKING` | `true` | 主套件是否传 `--thinking` |
| `KVV_PARALLEL_JOBS` | `4` | pytest-xdist 并发数 |
| `KVV_RERUNS` | `3` | 失败重试次数 |
| `KVV_RERUNS_DELAY` | `2` | 重试间隔秒数 |
| `KVV_OUT_DIR` | `outputs/<model>_<ts>` | 输出目录(Jenkins 固定注入 `builds/<tester>/<model>/<build>/`) |
| `KVV_CASE_DIR` | `testdata/walle_validator_cases/validator_cases` | tool 套件用例目录 |
| `KVV_SELECTION` | `all` | tool 套件选择模式 all/explicit/object |
| `KVV_MAX_CASES` | 空 | tool 套件用例数上限(空=不限) |
| `KVV_MAX_TOKENS` | `2048` | tool 套件单次响应最大 tokens |

`KVV_SUITE` 取值:

| 值 | 执行内容 |
|----|----------|
| `all` | 与本地脚本完全一致:params(opensource) + params(kimi) + 四个主套件合跑一轮(k3_features + prompt_tokens + tool_call_json_schema + toolcall_reject_cases) |
| `params` | 仅参数约束套件(仍按 opensource + kimi 双轮) |
| `k3_features` / `prompt_tokens` / `tool_call_json_schema` | 仅该套件单跑一轮(toolcall_reject_cases 无独立单跑入口,含在 `all` 中) |

除本地脚本的产物外,额外生成 `stats.properties`(供 Jenkins 邮件 stage
解析统计:total/passed/failed/skipped/errors/rate/result)。

---

## 5. Jenkins 流水线

打开 Jenkins job → Build with Parameters → 填端点、选套件 → 构建。
Jenkins 通过 ssh 远程到 `REMOTE_HOST`(默认 `10.201.132.50`),在
`WORK_DIR` 下执行 `run_kimivv_tests.sh`,完成后拉回报告、归档、发邮件。

### 5.1 stage 一览

| stage | 作用 |
|-------|------|
| 打印测试参数 | 回显所有入参(含 BASE_URL 拼接结果) |
| API 连通性预检 | curl `/v1/models` + `/v1/chat/completions`(真实模型发 hello);任一非 200 置 `CONNECTIVITY_FAILED`,后续运行/拉取 stage 跳过,构建置 UNSTABLE |
| 环境检查 | ① 清理残留进程(`pgrep -af "run_kimivv_tests\.sh\|Kimi-Vendor-Verifier"`,SIGTERM→3s→SIGKILL,排除 jenkins/durable/@tmp/kvv_bench/bench_builds,**不会误杀并发运行中的基准评测进程**) ② `git lfs pull`(经代理,参数 `HTTP_PROXY`) ③ 首次自动 `uv venv` + `uv pip install -r pyproject.toml`(不走代理,直连 pypi) |
| 运行测试 | `source .venv/bin/activate` 后注入全部环境变量,执行 `bash run_kimivv_tests.sh`;`catchError` 包裹,失败置 FAILURE 但不阻塞后续 stage |
| 拉取报告到Jenkins | 远程 `tar -czf` → `scp` 拉回 → 解压到本地 `reports/<build>/` |
| 发送邮件 | 解析 `stats.properties` 渲染统计表 + 提取 HTML 报告的 `<style>`/`<body>` 内嵌,附报告与日志;`catchError` 包裹,永远执行 |
| 清理旧构建 | 远程 `builds/<tester>/<model>/` 每个模型只保留最近 20 个构建目录(清空的模型目录移除) |

连通性预检失败时:运行/拉取 stage 被跳过,邮件照常发出,内含红色
告警框提示检查 BASE_URL / API_KEY。

同一 job 已通过 `disableConcurrentBuilds()` 禁止并发构建(否则新构建的
"清理残留进程"会误杀上一构建尚在运行的测试进程);与基准评测 job
(`JenkinsfileBench`)之间可以安全并发,双方清理步骤互相排除对方进程
(见 8.4)。

### 5.2 Jenkins 参数清单

| Jenkins 参数 | 透传目标 | 默认值 | 说明 |
|--------------|----------|--------|------|
| `TESTER` | 目录分层 | `liwt` | 测试人员,`builds/<tester>/<model>/<build>/` |
| `CHIP` | 邮件展示 | `nvidia-b300` | 芯片平台名称 |
| `ENGINE` | 邮件展示 | `sglang` | 推理框架(sglang/vllm) |
| `PD` | 邮件展示 | `agg` | PD 分离模式(agg=非PD分离,disagg=PD分离) |
| `SUITE` | `KVV_SUITE` | `all` | 测试套件选择 |
| `MODEL` | `MODEL_NAME` | `kimi-k3` | 模型服务名 |
| `BASE_URL` | `KIMI_BASE_URL`(经 `BASE_URL_V1`) | `https://token-bj11.alayanew.com:26443` | **根地址,不带 `/v1`**;流水线 `environment` 中 idempotent 拼接(剥尾斜杠→剥结尾 /v1→统一补 /v1) |
| `API_KEY` | `KIMI_API_KEY` | `EMPTY` | password 类型,未鉴权保持 EMPTY |
| `THINK_MODE` | `KVV_THINK_MODE` | `kimi` | 主套件 thinking 格式(kimi/opensource,上游已移除 none) |
| `THINKING` | `KVV_THINKING` | `true` | 主套件是否传 `--thinking` |
| `PARALLEL_JOBS` | `KVV_PARALLEL_JOBS` | `4` | 429 限流时可降为 2/1 |
| `RERUNS` | `KVV_RERUNS` | `3` | 失败重试次数 |
| `CASE_DIR` | `KVV_CASE_DIR` | `testdata/walle_validator_cases/validator_cases` | tool 套件用例目录 |
| `SELECTION` | `KVV_SELECTION` | `all` | tool 套件选择模式 |
| `MAX_CASES` | `KVV_MAX_CASES` | 空 | 空 = 不限制 |
| `MAX_TOKENS` | `KVV_MAX_TOKENS` | `2048` | tool 套件单次响应上限 |
| `HTTP_PROXY` | LFS 拉取代理 | `http://<proxy-host>:<port>`(占位符) | 仅用于 `git lfs pull`;依赖安装直连;空/占位符未替换 = 直连 |
| `DESCRIPTION` | 邮件展示 | 空 | 模型服务描述 |
| `RECIPIENTS` | 邮件收件人 | `liwt@zetyun.com` | 逗号分隔 |
| `WORK_DIR` | 远程目录 | `/dingofs/data2/userdata/liwt/maas-image/Kimi-Vendor-Verifier` | 远程仓库目录,请勿改动 |

### 5.3 远程目录与产物层级

```
<WORK_DIR>/builds/<tester>/<model>/<build_number>/      # KVV_OUT_DIR(Jenkins 注入,model 名安全化)
├── kkv_base_test_<ts>.log                          # 全程总日志(邮件附件)
├── junit_params_opensource.xml
├── junit_params_kimi.xml
├── junit_main.xml
├── tool_call_schema_report.json
├── <model>_kvv_preflight_test_report_<ts>.html         # HTML 汇总报告(邮件附件+内嵌)
└── stats.properties                                # 邮件统计解析用

<Jenkins 工作空间>/reports/<build_number>/          # 拉取报告到Jenkins后
└── (上述全部产物)
```

---

## 6. 邮件通知

- **触发条件**:`发送邮件` stage 永远执行(`catchError` 包裹,失败不阻塞 build)
- **数据源**:`stats.properties`(由 `run_kimivv_tests.sh` 结尾生成),
  含 total/passed/failed/skipped/errors/rate/result
- **正文构成**:结果横幅(PASS 绿 / FAIL 红) + Jenkins 构建信息表 +
  HTML 报告正文(从 `<model>_kvv_preflight_test_report_<ts>.html` 提取 `<style>` 与
  `<body>` 内嵌,与本地报告格式完全一致) + 构建链接
- **连通性失败**:红色告警框提示未执行测试,统计列显示 N/A
- **附件**:HTML 报告 + 全程日志

---

## 7. 故障排查

| 现象 | 排查路径 |
|------|----------|
| 大量 429 / 请求限流 | `PARALLEL_JOBS` 降到 2 或 1,必要时提高 `RERUNS` |
| `prompt_tokens` 收集失败/缺数据 | 远程执行 `git lfs pull`;确认 git-lfs 已安装(环境检查 stage 有告警) |
| 远程 venv 缺包/依赖损坏 | 删 `<WORK_DIR>/.venv` 重跑,环境检查 stage 自动 `uv venv` + `uv pip install -r pyproject.toml` |
| 连通性预检失败 | 看构建日志中 curl 的 HTTP 状态码与响应;核对 BASE_URL(不带 /v1)与 API_KEY |
| 邮件统计全 N/A | `find reports/<build> -name stats.properties` 确认是否拉回;没有则看运行测试 stage 日志 |
| 怀疑上一次构建残留进程干扰 | 环境检查 stage 开头自动清理;也可远程手工 `pgrep -af "run_kimivv_tests\.sh\|Kimi-Vendor-Verifier"` 检查 |
| 想本地复现 Jenkins 行为 | 按第 4 节表格手工 export 环境变量后 `./run_kimivv_tests.sh`,或直接 `bash run_kimivv_tests.sh` 用默认值 |

---

## 8. 基准评测:`JenkinsfileBench`

与功能验证(`Jenkinsfile`)完全独立的第二条流水线,执行 inspect-ai 基准
评测。评测任务、思考强度全部参数化,默认值取自
`README.md`「Recommended Benchmark Parameters (K3)」推荐表。

### 8.1 评测任务与推荐默认值

任务级配置(样本上限、单次响应最大 tokens)采用 **JSON 参数**(风格与
evalscope-test 流水线的 `TASK_MAX_TOKENS_JSON` 一致):`TASK_LIMIT_JSON`
(样本上限,值 `null`=全量,默认
`{"ocrbench": null, "mmmu": null, "kimi_toolcall": null, "beam": null}`)与
`TASK_MAX_TOKENS_JSON`(单次响应最大 tokens,默认
`{"ocrbench": 16384, "mmmu": 65536, "kimi_toolcall": 32768, "beam": 65536}`)。
每个维度一个 JSON,键=评测任务名,**须覆盖所有勾选任务**,运行评测开始时统一
校验,缺键/非法则快速失败。下表为两个 JSON 参数的默认值(MMMU 为本地口径
65536,其余为 K3 推荐口径):

| 评测任务 | 数据来源 | 样本上限默认 | MaxTokens 默认 | 说明 |
|----------|----------|--------------|----------------|------|
| OCRBench | HF `echo840/OCRBench`(首次经代理下载并缓存) | 空(全量 1000) | 16384 | 文本识别基准,单图短答 |
| MMMU | HF `MMMU/MMMU_Pro` standard (10 options)(数 GB,首次下载慢) | 空(全量约 1.17 万) | 65536(上游推荐 98304,本地口径下调) | 多模态选择题,1~7 图/题;全量单轮数亿 token,请预留充足时长,快速验证时可用 `TASK_LIMIT_JSON` 截断 |
| ToolCall | 仓库内 LFS `toolcall_benchmark/toolcall_thinking_samples.stable2.jsonl`(全量 70 样本) | 空(全量) | 32768 | 即 README K3 表「Tool Call」(`kimi_toolcall`),评分 tool_calls 格式/合法性;**默认勾选** |
| BEAM | 仓库内 LFS `beam/data/beam_1m_chats.jsonl.gz`(35 对话×20 题=700 题,另有 128k 变体) | 空(全量 700) | 65536 | 1M 长程记忆基准,独立两阶段脚本(生成+LLM 判分),**默认不勾选**;详见 8.4 |

思考强度为**布尔多选**(`EFFORT_NONE` / `EFFORT_LOW` / `EFFORT_HIGH` /
`EFFORT_MAX`,默认仅勾选 `max`):每个勾选的强度对每个勾选的评测任务
各跑一轮,组合数 = 任务数 × 强度数;`none` 即 non-thinking 轮,但
`kimi_toolcall` 数据集为 thinking-only(上游无 non-thinking 数据集,K3 表
也只有 low/high/max 三行),该组合在评测项校验与运行循环中**自动跳过**;
若最终有效组合为空则快速失败。

其余暴露参数:`THINK_MODE`(kimi/opensource)、`TEMPERATURE`(1.0)、
`TOP_P`(0.95)、`EPOCHS`(1)、`MAX_CONNECTIONS`(8,SGLang 自部署请勿
照抄官方 50)、`CLIENT_TIMEOUT`(86400)、`STREAM`(true)、`HTTP_PROXY`
(默认已填内网代理,可留空(空 = 直连);仅用于 LFS 拉取/HF 数据集首次
下载,**依赖安装不走代理**;评测阶段自动剥离,模型请求全直连)、
`HF_ENDPOINT`(可选镜像)、`TOOLCALL_DATASET`(kimi_toolcall
数据集路径)、BEAM 组参数(`RUN_BEAM` 默认不勾选;`BEAM_DATA` 1m/128k;
`BEAM_PLAIN_TEXT` 默认 true;`BEAM_CONCURRENCY` 8;`BEAM_TIMEOUT` 3600s;
`BEAM_DRY_RUN` 冒烟;判分 `JUDGE_BASE_URL`/`JUDGE_API_KEY`/`JUDGE_MODEL`
默认 kimi-k2.6/`JUDGE_TEMPERATURE` 默认 1.0(kimi 系判分模型须 1.0)/
`JUDGE_CONCURRENCY` 10,判分端点留空=仅生成 answers)、
`CHIP`/`ENGINE`/`PD`(芯片平台/推理框架/PD 分离模式,仅
邮件展示,与功能验证流水线共用同一组参数定义)。

### 8.2 stage 一览

| stage | 作用 |
|-------|------|
| 打印评测参数 | 回显所有入参(含 BASE_URL 拼接结果) |
| 评测项校验 | 任务/强度至少各勾选一个,否则快速失败;打印将执行的组合清单 |
| API 连通性预检 | 同 `Jenkinsfile`(models + chat/completions),失败置 UNSTABLE 并跳过后续 |
| 环境准备 | 清理残留评测进程(`pgrep -af "kvv_bench_runner\|run_kimivv_bench\|beam_generate\|beam_judge"`,排除 run_kimivv_tests,**不会误杀并发运行中的功能验证进程**) → `git lfs pull`(经代理,含 kimi_toolcall 与 BEAM 数据) → `uv venv` + `uv pip install -r pyproject.toml`(不走代理,直连 pypi) |
| 运行评测 | 评测脚本经 ssh 上传到远程后由 `nohup` **后台执行**(MMMU 全量 ~50 小时,不能由单条长连接 ssh 承载),Jenkins 侧每 5 分钟用**短连接轮询** exit-code 标记并回显日志尾部,网络抖动只损失单次轮询;脚本内 `source .venv/bin/activate` → **补齐 uv PATH**(nohup 经 `ssh "<cmd>"` 为非登录 shell,不加载 profile,uv 在 `~/.local/bin` 不在默认 PATH;显式 export + profile 兜底 + 缺失快速失败,否则 `uv run` 全部 127) → 校验 `TASK_LIMIT_JSON`/`TASK_MAX_TOKENS_JSON`(缺键/非法快速失败) → 双层循环(任务×强度)按 JSON 解析每任务的 limit/max_tokens 调 `run_kimivv_bench.sh`(beam 走脚本内两阶段分支),单组合失败不中断;结束后 `kvv_bench_runner.py aggregate` 生成 `bench_summary.properties`,再由 `generate_bench_report.py` 生成 HTML 报告 `<model>_kvv_bench_test_report_<时间戳>.html`(落在 bench 目录内随结果拉回,失败仅告警不影响构建结论)。任何退出路径(含 set -e 中断)都写 exit-code 标记,step 重试时按 pid 检测避免双跑 |
| 拉取结果到Jenkins | tar → scp → 解包 `reports/<build>/`(含 `bench/<bench>/<effort>/summary.json` 与 inspect 日志、`bench_run_<N>.log/sh/rc`、HTML 报告);**显式校验 bench_summary.properties 已包含**,缺失时打警告(邮件将不显示汇总) |
| 发送邮件 | accuracy 汇总表(任务×强度)+ 构建信息;基准为能力测量,无 PASS/FAIL 阈值;附件含各组合 bench 日志、`bench_summary.properties`、HTML 报告与 `bench_run_<N>.log` |
| 清理旧构建 | 远程 `bench_builds/<tester>/<model>/` 每个模型只保留最近 20 个构建目录(独立命名空间,不触碰功能验证的 `builds/`) |

### 8.3 环境准备与虚拟环境

**所有评测任务强制在 uv 虚拟环境中执行**,与功能验证流水线(第 3 节)
共用同一套三层保证:

1. **创建/同步**(`JenkinsfileBench` 环境准备 stage):远程仓库根目录若无
   `.venv` 则自动 `uv venv` 创建,随后 `uv pip install -r pyproject.toml`
   同步依赖(不走代理,直连 pypi);
2. **激活**(运行评测 stage):每轮循环前 `source .venv/bin/activate`;
3. **守卫**(`run_kimivv_bench.sh` 开头,见 `run_kimivv_bench.sh:37`):
   - `VIRTUAL_ENV` 已设置(已在 venv 内) → 跳过;
   - 未激活但仓库根目录存在 `.venv/bin/activate` → 自动 `source`
     (日志打印 `Activated uv venv: ...`);
   - 连 `.venv` 都没有 → 报错退出(`exit 1`),提示先执行
     `uv venv && uv pip install -r pyproject.toml`,不会静默跑在系统环境。

最终执行命令为 `uv run --no-sync python kvv_bench_runner.py ...`:
`--no-sync` 锁定当前激活的 venv,不访问网络、不改依赖。

**本地手动跑基准评测**的环境准备(与功能验证共用 `.venv`,依赖一致):

```bash
# 1. 首次:创建并同步虚拟环境(.venv 已存在则跳过)
uv venv
source .venv/bin/activate
uv pip install -r pyproject.toml

# 2. kimi_toolcall 需要仓库内 LFS 数据集
git lfs pull

# 3. 配置模型与评测组合(示例)
export KIMI_BASE_URL="http://127.0.0.1:3000/v1"
export KIMI_API_KEY="EMPTY"
export MODEL_NAME="kimi-k3"
export KVB_BENCH="ocrbench"
export KVB_EFFORT="max"
export KVB_OUT_DIR="/tmp/kvv_bench_out"

# 4. 执行
./run_kimivv_bench.sh

# 5. BEAM(1M) 示例(可选;判分端点留空则仅生成 answers)
export KVB_BENCH="beam"
export KVB_EFFORT="max"
export KVB_BEAM_DATA="1m"
export KVB_LIMIT="5"                       # 冒烟:仅前 5 题
export KVB_JUDGE_BASE_URL="http://127.0.0.1:3001/v1"
export KVB_JUDGE_API_KEY="EMPTY"
./run_kimivv_bench.sh
```

OCRBench/MMMU 首次运行会从 HuggingFace 下载数据集(需 `KVB_HTTP_PROXY`
或 `KVB_HF_ENDPOINT`),之后走本地缓存。

### 8.4 关键说明

- **与功能验证流水线的产物隔离**:两条流水线是独立 Jenkins job,
  BUILD_NUMBER 各自计数、**完全可能相同**,因此远程产物命名空间彻底
  分开——功能验证用 `builds/<tester>/<model>/<N>`,基准评测用
  `bench_builds/<tester>/<model>/<N>`,各自的"保留最近 20 个构建"清理只作用于
  自己的命名空间;`/tmp` 中转包分别为 `kvv_<N>.tar.gz` 与
  `kvv_bench_<N>.tar.gz`,两 job 同号构建并发也不会互相覆盖。Jenkins
  控制器侧的 `reports/<N>/` 位于各 job 自己的 workspace,天然隔离。
- **进程清理互不误杀**:"清理残留进程"只匹配本流水线自身的进程特征,
  并显式排除对方进程——功能验证排除 `kvv_bench`/`bench_builds`
  (基准评测的 runner/包装脚本/tar 拉取都带这些标识),基准评测排除
  `run_kimivv_tests`。因此一条流水线触发时**不会**杀掉另一条正在
  运行的流水线进程(修复前功能验证的模式含仓库名 `Kimi-Vendor-Verifier`,
  会误杀带绝对 out-dir 参数的 `kvv_bench_runner.py`)。排除项全部是
  对方独有、自身命令行不可能出现的标识,**不影响清理各自残留进程**
  (功能验证的 pytest 经 `--junitxml` 绝对路径携带仓库名,基准评测的
  runner/包装脚本本身即含 `kvv_bench` 标识)。两个 job 均已
  设置 `disableConcurrentBuilds()`,同一 job 自身也不会并发构建。
- **代理与评测隔离**:`HTTP_PROXY` 仅在两处生效——环境准备的
  `git lfs pull`、运行评测时 `kvv_bench_runner.py` 构建任务的
  数据集加载阶段(覆盖 HuggingFace 下载)。**依赖安装(`uv pip install`)
  不走代理**:代理会掐断 pypi 的 TLS 握手(`tls handshake eof`),
  因此两条流水线的依赖安装均改为始终直连。数据集就绪后、发起任何
  模型请求前,`kvv_bench_runner.py` 自动剥离 `https_proxy`/`http_proxy`
  等代理环境变量,评测全程直连。数据已存在本地则不会重复拉取:
  `git lfs pull` 按对象增量跳过,HF `load_dataset` 走
  `~/.cache/huggingface` 缓存。占位符 `http://<proxy-host>:<port>`
  未替换时一律按直连处理。
- **为什么不直接用 `eval.py`**:上游 CLI 未对 OCRBench/MMMU 暴露
  `--limit`(无法按需截断 MMMU 约 1.17 万样本的全量),且无机读输出。
  `kvv_bench_runner.py` 只做编排,**不改动任何上游文件**;LIMIT 参数
  提供可选截断,默认留空跑全量。
- **ToolCall 与 K3 表对齐**:即 README「K3」推荐表的 `Tool Call` 基准,
  推荐口径 temp 1.0 / top_p 0.95 / max_tokens 32768 / epochs 1(流水线
  默认值一致);上游 `eval.py` 对该任务的内置默认是 epochs=5 /
  max_connections=5,本流水线以显式参数覆盖为 K3 表口径。
  评测复放真实对话历史并注入 tools,模型只做一次生成,评分针对
  tool_calls 的 JSON 合法性、schema 校验、幻觉工具、控制符泄漏等。
- **HuggingFace 数据集**:首次运行经 `HTTP_PROXY` 下载并缓存在远程主机
  `~/.cache/huggingface`,之后不再依赖网络。
- **BEAM(1M) 已接入(可选,默认关闭)**:长程记忆基准(35 段对话×20 探测题
  =700 题,单题上下文可达 1M token),官方 K3/K3.1 供应商验证表的正式列。
  与其他基准不同,BEAM 为上游 `beam/` 目录的独立两阶段脚本(不改动上游
  代码):`beam_generate.py` 生成 answers(断点续跑)→ `beam_judge.py`
  LLM-as-Judge 按 rubric 判分(断点续跑)→ `kvv_bench_runner.py
  beam-summary` 产出与其他基准同构的 `summary.json`,经既有 aggregate
  自动进入 `bench_summary.properties` 与邮件表格(行名 `beam/<effort>`)。
  接入方式:勾选 `RUN_BEAM` 后,beam 与其他基准共用 (bench,effort) 组合
  循环,limit/max_tokens 走既有 `TASK_LIMIT_JSON`/`TASK_MAX_TOKENS_JSON`
  的 `beam` 键(默认 null=700 全量 / 65536);thinking 语义与其他基准
  完全同源(`get_thinking_extra_body`,effort 优先,透传 `--thinking-json`;
  effort=none 时为 non-thinking 轮,与上游 CI 一致)。
  - **产物**:`bench/beam/<effort>/answers_<mode>.jsonl`、
    `scores_<mode>.jsonl`、`summary.json`(`<mode>`=plain|multiturn;
    切换 `BEAM_PLAIN_TEXT` 时文件名不同,断点续跑互不干扰;两阶段脚本
    均自动跳过已完成题目,中断后重跑即可续跑)。
  - **前提**:被测端点须接受 ~1M token 输入(建议先 `BEAM_DRY_RUN` 或
    `TASK_LIMIT_JSON.beam=1` 冒烟验证;上下文不足 1M 的部署可切
    `BEAM_DATA=128k`);判分端点须独立于被测端点且 Jenkins 节点可直连
    (beam 分支在生成前剥离全部代理变量,生成与判分均直连;判分模型为
    kimi 系时 `JUDGE_TEMPERATURE` 须为 1.0)。
  - **资源**:1M 全量约 7 亿输入 token、生成数小时,勾选后建议仅保留
    一个思考强度(校验阶段多强度勾选时会打印 WARN);判分端点留空时
    仅生成 answers(跳过判分与汇总,不出 accuracy 结果行);
    `BEAM_DRY_RUN` 同样跳过判分与汇总。判分失败(或判分行数少于答案
    行数)时该组合标记 FAILED,不影响其他组合继续执行。
- **邮件"未生成评测汇总"的真正根因(build #5/#6 均中招)**:邮件 stage 解析
  properties 的正则把行尾锚点写成 `\$`——Groovy 斜杠字符串中 `\$` 会**保留
  反斜杠**,正则含义变成"匹配字面 '$' 字符",任何 key 都匹配不上 → 结果表恒为
  "未生成评测汇总"。build #5 控制台日志证明:汇总文件已拉取到 workspace、
  `fileExists`/`readFile` 均成功执行,症结**仅**在正则(行尾锚点须写裸 `$`,
  与功能验证流水线 `Jenkinsfile:276` 的写法一致)。同批修复的连带问题:
  environment 块 `BASE_URL_V1` 的 `replaceAll('/+\$','')` 同因永不生效(改为
  `'/+$'`,尾斜杠/尾 /v1 剥离恢复工作);表格取值原用 GString 作 map key,
  因 hashCode 与 String 不一致会查不到(改为 String 拼接);TreeSet 元素
  补 `.toString()` 防 ClassCastException。
- **长时评测的抗断线加固**:旧实现用单条 ssh 长连接承载全部评测,MMMU 全量
  约 50 小时,网络闪断会杀死 Jenkins 侧步骤(catchError 降级为 UNSTABLE 后
  流水线继续,拉取到的将是未完成快照)。现改为**远程 nohup 后台执行 +
  短连接轮询**(见 8.2),断网只损失单次轮询、不再影响评测执行与拉取时机;
  拉取 stage 显式校验汇总文件存在性,邮件缺行时提示查看附件
  `bench_run_<N>.log`(后台执行的完整控制台输出)。手动 abort 构建会由
  运行评测 stage 的 `post { aborted }` 钩子按 pidfile 递归终止远程进程树
  (先 TERM 宽限 10 秒再 KILL 残留,仅覆盖本构建的 PID 及其子孙,不影响
  其他构建),不会留下孤儿评测进程。
- **AIME 2025**:`eval.py` 支持(`epochs=32`),K3 已不推荐,未纳入本流水线。

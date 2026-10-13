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

两个入口脚本**相互独立、互不影响**:

- `run_preflight_tests.sh`:不读任何环境变量,所有配置(`KIMI_BASE_URL` /
  `MODEL_NAME` / `THINK_MODE` / 并发数等)写死在脚本顶部,改脚本即改配置;
- `run_kimivv_tests.sh`:不写死任何业务配置,全部从环境变量取值
  (带默认值兜底),由 Jenkinsfile 的 `运行测试` stage 统一注入。

两个脚本均已通过 git index 记录可执行位(`100755`),`git clone` 之后
直接 `./run_preflight_tests.sh` / `./run_kimivv_tests.sh` 即可执行。

---

## 2. 测试套件与 think-mode

### 2.1 四个测试套件

| 套件目录 | 验证内容 |
|----------|----------|
| `tests/params` | 参数约束:不可变参数(temperature/top_p/presence_penalty/frequency_penalty/n)默认值必须被接受、错误值必须被 400 拒绝 |
| `tests/k3_features` | K3 特性:动态工具、response_format、thinking effort、tool_choice、tokenization groundtruth |
| `tests/prompt_tokens` | prompt token 计数与 groundtruth 比对(测试数据走 git-lfs) |
| `tests/tool_call_json_schema` | 工具调用 JSON Schema 校验(walle validator cases) |

### 2.2 think-mode 三种格式

`--think-mode` 控制请求中 thinking 参数的**格式**(三处实现一致:
`tests/params/test_params.py` 与 `tests/tool_call_json_schema/validator.py`):

| think-mode | 请求 extra_body | 适用 |
|------------|----------------|------|
| `opensource` | `{"chat_template_kwargs": {"thinking": true/false}}` | vLLM/SGLang 等开源部署 |
| `kimi` | `{"thinking": {"type": "enabled"/"disabled"}}` | Kimi 官方 API 格式 |
| `none` | `{}`(完全不传) | 两种格式都不支持的 vendor |

### 2.3 params 套件必须双轮跑

`tests/params` 的目的是**验证参数本身**,因此无论 THINK_MODE 设成什么,
该套件固定按 `opensource` 和 `kimi` 两种格式各跑一轮(两个入口脚本行为
一致),分别产出独立的 junit XML。其余三个套件只需按目标部署实际支持的
格式选一种发请求,由 `THINK_MODE` 决定。

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
| 3/3 | `pytest tests/k3_features tests/prompt_tokens tests/tool_call_json_schema --think-mode ${THINK_MODE} --thinking --tool-json-report ...` | `junit_main.xml` |

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
└── kkv_base_test_20261012123045_report.html    # HTML 汇总报告
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
| `KVV_OUT_DIR` | `outputs/<model>_<ts>` | 输出目录(Jenkins 固定注入 `builds/<tester>/<build>/`) |
| `KVV_CASE_DIR` | `testdata/walle_validator_cases/validator_cases` | tool 套件用例目录 |
| `KVV_SELECTION` | `all` | tool 套件选择模式 all/explicit/object |
| `KVV_MAX_CASES` | 空 | tool 套件用例数上限(空=不限) |
| `KVV_MAX_TOKENS` | `2048` | tool 套件单次响应最大 tokens |

`KVV_SUITE` 取值:

| 值 | 执行内容 |
|----|----------|
| `all` | 与本地脚本完全一致:params(opensource) + params(kimi) + 三主套件合跑一轮 |
| `params` | 仅参数约束套件(仍按 opensource + kimi 双轮) |
| `k3_features` / `prompt_tokens` / `tool_call_json_schema` | 仅该套件单跑一轮 |

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
| 环境检查 | ① 清理残留进程(`pgrep -af "run_kimivv_tests\.sh\|Kimi-Vendor-Verifier"`,SIGTERM→3s→SIGKILL,排除 jenkins/durable/@tmp) ② `git restore .` + `git pull` + `git lfs pull` ③ 首次自动 `uv venv` + `uv pip install -r pyproject.toml` |
| 运行测试 | `source .venv/bin/activate` 后注入全部环境变量,执行 `bash run_kimivv_tests.sh`;`catchError` 包裹,失败置 FAILURE 但不阻塞后续 stage |
| 拉取报告到Jenkins | 远程 `tar -czf` → `scp` 拉回 → 解压到本地 `reports/<build>/` |
| 发送邮件 | 解析 `stats.properties` 渲染统计表 + 提取 HTML 报告的 `<style>`/`<body>` 内嵌,附报告与日志;`catchError` 包裹,永远执行 |
| 清理旧构建 | 远程 `builds/<tester>/` 只保留最近 20 个构建目录 |

连通性预检失败时:运行/拉取 stage 被跳过,邮件照常发出,内含红色
告警框提示检查 BASE_URL / API_KEY。

### 5.2 Jenkins 参数清单

| Jenkins 参数 | 透传目标 | 默认值 | 说明 |
|--------------|----------|--------|------|
| `TESTER` | 目录分层 | `liwt` | 测试人员,`builds/<tester>/<build>/` |
| `SUITE` | `KVV_SUITE` | `all` | 测试套件选择 |
| `MODEL` | `MODEL_NAME` | `kimi-k3` | 模型服务名 |
| `BASE_URL` | `KIMI_BASE_URL`(经 `BASE_URL_V1`) | `http://127.0.0.1:3000` | **根地址,不带 `/v1`**;流水线 `environment` 中 idempotent 拼接(剥尾斜杠→剥结尾 /v1→统一补 /v1) |
| `API_KEY` | `KIMI_API_KEY` | `EMPTY` | password 类型,未鉴权保持 EMPTY |
| `THINK_MODE` | `KVV_THINK_MODE` | `kimi` | 主套件 thinking 格式(kimi/opensource/none) |
| `THINKING` | `KVV_THINKING` | `true` | 主套件是否传 `--thinking` |
| `PARALLEL_JOBS` | `KVV_PARALLEL_JOBS` | `4` | 429 限流时可降为 2/1 |
| `RERUNS` | `KVV_RERUNS` | `3` | 失败重试次数 |
| `CASE_DIR` | `KVV_CASE_DIR` | `testdata/walle_validator_cases/validator_cases` | tool 套件用例目录 |
| `SELECTION` | `KVV_SELECTION` | `all` | tool 套件选择模式 |
| `MAX_CASES` | `KVV_MAX_CASES` | 空 | 空 = 不限制 |
| `MAX_TOKENS` | `KVV_MAX_TOKENS` | `2048` | tool 套件单次响应上限 |
| `DESCRIPTION` | 邮件展示 | 空 | 模型服务描述 |
| `RECIPIENTS` | 邮件收件人 | `liwt@zetyun.com` | 逗号分隔 |
| `WORK_DIR` | 远程目录 | `/dingofs/data2/userdata/liwt/maas-image/Kimi-Vendor-Verifier` | 远程仓库目录,请勿改动 |

### 5.3 远程目录与产物层级

```
<WORK_DIR>/builds/<tester>/<build_number>/          # KVV_OUT_DIR(Jenkins 注入)
├── kkv_base_test_<ts>.log                          # 全程总日志(邮件附件)
├── junit_params_opensource.xml
├── junit_params_kimi.xml
├── junit_main.xml
├── tool_call_schema_report.json
├── kkv_base_test_<ts>_report.html                  # HTML 汇总报告(邮件附件+内嵌)
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
  HTML 报告正文(从 `kkv_base_test_<ts>_report.html` 提取 `<style>` 与
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

pipeline {
    agent {
        label 'master'
    }

    parameters {
        string(name: 'TESTER', defaultValue: 'liwt', description: '测试人员名称（必填）')
        string(name: 'MODEL', defaultValue: 'kimi-k3', description: '模型服务名称（必填，透传为环境变量 MODEL_NAME，即 /v1/models 返回的 id）')
        string(name: 'BASE_URL', defaultValue: 'http://127.0.0.1:3000', description: 'API 根地址（必填，不带 /v1 后缀，流水线会自动拼接，透传为环境变量 KIMI_BASE_URL）')
        password(name: 'API_KEY', defaultValue: 'EMPTY', description: 'API Key（透传为环境变量 KIMI_API_KEY；SGLang 未开启鉴权时保持 EMPTY）')

        choice(name: 'SUITE', choices: ['all', 'params', 'k3_features', 'prompt_tokens', 'tool_call_json_schema'], description: '测试套件：all=全量（与本地 run_preflight_tests.sh 一致，params 按 opensource+kimi 两轮执行）；params=仅参数约束套件（opensource+kimi 两轮）；其余为执行指定单个套件')
        choice(name: 'THINK_MODE', choices: ['kimi', 'opensource', 'none'], description: '主套件（k3_features/prompt_tokens/tool_call_json_schema）thinking 参数格式（--think-mode，默认 kimi）')
        booleanParam(name: 'THINKING', defaultValue: true, description: 'tool 套件是否开启 thinking（--thinking）')
        string(name: 'PARALLEL_JOBS', defaultValue: '4', description: 'pytest-xdist 并发数（429 限流时可降低）')
        string(name: 'RERUNS', defaultValue: '3', description: '失败用例重试次数')

        string(name: 'CASE_DIR', defaultValue: 'testdata/walle_validator_cases/validator_cases', description: 'tool 套件 walle validator_cases 目录（--case-dir）')
        choice(name: 'SELECTION', choices: ['all', 'explicit', 'object'], description: 'tool 套件用例选择模式（--selection）')
        string(name: 'MAX_CASES', defaultValue: '', description: 'tool 套件用例数上限（空 = 不限制，--max-cases）')
        string(name: 'MAX_TOKENS', defaultValue: '2048', description: 'tool 套件单次响应最大 tokens（--max-tokens）')

        string(name: 'DESCRIPTION', defaultValue: '', description: '模型服务的描述信息（仅用于邮件展示）')
        text(name: 'RECIPIENTS', defaultValue: 'liwt@zetyun.com', description: '测试报告邮件接收者（逗号分隔）')
        string(name: 'WORK_DIR', defaultValue: '/dingofs/data2/userdata/liwt/maas-image/Kimi-Vendor-Verifier', description: '测试仓库目录，请不要改动')
    }

    environment {
        SSH_CREDENTIALS = 'HOST_SSH_KEY'
        REMOTE_HOST = '10.201.132.50'
        REMOTE_USER = 'root'
        BUILD_OUTPUT_DIR = "builds/${params.TESTER}/${BUILD_NUMBER}"
        // 用户在 BASE_URL 填根地址（可不带或带 /v1，可带或不带尾斜杠）。
        // 这里 idempotent 拼接出唯一的 OpenAI 兼容端点：
        //   先剥尾斜杠 → 再剥结尾 /v1（若有）→ 统一补 /v1
        // 例： http://h:3000 | http://h:3000/ | http://h:3000/v1 | http://h:3000/v1/
        //     都得到 http://h:3000/v1
        BASE_URL_V1 = "${params.BASE_URL.replaceAll('/+\$', '').replaceAll('/?v1\$', '')}/v1"
    }

    stages {
        stage('打印测试参数') {
            steps {
                script {
                    println("========================================")
                    println("=== KVV 测试参数信息 ===")
                    println("========================================")
                    println("测试人员:     ${params.TESTER}")
                    println("测试套件:     ${params.SUITE}")
                    println("模型名称:     ${params.MODEL}")
                    println("BASE_URL:     ${params.BASE_URL}  (→ ${env.BASE_URL_V1})")
                    println("ThinkMode:    ${params.THINK_MODE}")
                    println("Thinking:     ${params.THINKING}")
                    println("并发数:       ${params.PARALLEL_JOBS}")
                    println("重试次数:     ${params.RERUNS}")
                    println("Case目录:     ${params.CASE_DIR}")
                    println("Selection:    ${params.SELECTION}")
                    println("MaxCases:     ${params.MAX_CASES ?: '无限制'}")
                    println("MaxTokens:    ${params.MAX_TOKENS}")
                    println("模型描述:     ${params.DESCRIPTION ?: '(无)'}")
                    println("邮件接收者:   ${params.RECIPIENTS}")
                    println("工作目录:     ${params.WORK_DIR}")
                    println("构建编号:     #${BUILD_NUMBER}")
                    println("========================================")
                }
            }
        }

        stage('API 连通性预检') {
            steps {
                sshagent(credentials: ["${SSH_CREDENTIALS}"]) {
                    script {
                        def apiKey = params.API_KEY?.toString()?.trim()
                        if (!apiKey) { apiKey = 'EMPTY' }
                        try {
                            sh """
ssh -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} << 'ENDSSH'
set -o pipefail
{
    echo "=== 检查 API 连通性 (/v1/models) ==="
    HTTP_CODE=\$(curl -s --connect-timeout 10 -m 30 -o /dev/null -w "%{http_code}" -H "Authorization: Bearer ${apiKey}" ${env.BASE_URL_V1}/models)
    if [ "\${HTTP_CODE}" != "200" ]; then
        echo "ERROR: API 连通性检查失败, HTTP状态码: \${HTTP_CODE}, URL: ${env.BASE_URL_V1}/models"
        exit 1
    fi
    echo "API /models 连通性检查通过, HTTP状态码: \${HTTP_CODE}"

    echo "=== 检查 Chat Completions 接口 ==="
    CHAT_RESP=\$(curl -s --connect-timeout 10 -m 60 -w "\\n%{http_code}" ${env.BASE_URL_V1}/chat/completions \\
        -H "Content-Type: application/json" \\
        -H "Authorization: Bearer ${apiKey}" \\
        -d '{"model":"${params.MODEL}","messages":[{"role":"user","content":"hello"}],"max_tokens":10}')
    CHAT_HTTP_CODE=\$(echo "\${CHAT_RESP}" | tail -1)
    if [ "\${CHAT_HTTP_CODE}" != "200" ]; then
        echo "ERROR: Chat Completions 接口检查失败, HTTP状态码: \${CHAT_HTTP_CODE}"
        echo "响应内容: \$(echo "\${CHAT_RESP}" | head -n -1)"
        exit 1
    fi
    echo "Chat Completions 接口检查通过, HTTP状态码: \${CHAT_HTTP_CODE}"
} 2>&1 | tee /tmp/kvv_connectivity_${BUILD_NUMBER}.log
ENDSSH
"""
                        } catch (Exception e) {
                            env.CONNECTIVITY_FAILED = 'true'
                            currentBuild.result = 'UNSTABLE'
                            println("=== API 连通性预检失败,后续阶段(环境检查、运行测试、拉取报告)将跳过 ===")
                        }
                    }
                }
            }
        }

        stage('环境检查') {
            when {
                expression { env.CONNECTIVITY_FAILED != 'true' }
            }
            steps {
                sshagent(credentials: ["${SSH_CREDENTIALS}"]) {
                    sh """
ssh -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} << 'ENDSSH'
set -e
cd ${params.WORK_DIR}
echo "工作目录: \$(pwd)"

echo "=== 清理残留进程 ==="
RESIDUAL=\$(pgrep -af "run_kimivv_tests\\.sh|Kimi-Vendor-Verifier" 2>/dev/null | grep -vE "jenkins|durable|@tmp" || true)
if [ -n "\${RESIDUAL}" ]; then
    echo "发现残留进程:"
    echo "\${RESIDUAL}"
    echo "发送 SIGTERM..."
    echo "\${RESIDUAL}" | awk '{print \$1}' | xargs -r kill -TERM 2>/dev/null || true
    sleep 3
    REMAINING=\$(pgrep -af "run_kimivv_tests\\.sh|Kimi-Vendor-Verifier" 2>/dev/null | grep -vE "jenkins|durable|@tmp" || true)
    if [ -n "\${REMAINING}" ]; then
        echo "残留进程未响应 SIGTERM,发送 SIGKILL..."
        echo "\${REMAINING}" | awk '{print \$1}' | xargs -r kill -KILL 2>/dev/null || true
        sleep 1
    fi
    FINAL=\$(pgrep -af "run_kimivv_tests\\.sh|Kimi-Vendor-Verifier" 2>/dev/null | grep -vE "jenkins|durable|@tmp" || true)
    if [ -n "\${FINAL}" ]; then
        echo "WARN: 以下残留进程仍存在,需人工介入:"
        echo "\${FINAL}"
    else
        echo "残留进程清理完成"
    fi
else
    echo "未发现残留进程"
fi

echo "=== 同步代码 ==="
export https_proxy=http://10.201.136.68:1080
export http_proxy=http://10.201.136.68:1080
if command -v git-lfs >/dev/null 2>&1; then
    git lfs pull
else
    echo "警告: git-lfs 不可用，prompt_tokens 套件可能因缺少 LFS 测试数据而收集失败"
fi
unset https_proxy
unset http_proxy

echo "=== 检查并创建虚拟环境 ==="
if [ ! -d ".venv" ]; then
    echo "虚拟环境不存在，创建虚拟环境..."
    uv venv
fi
echo "同步依赖..."
export https_proxy=http://10.201.136.68:1080
export http_proxy=http://10.201.136.68:1080
source .venv/bin/activate
uv pip install -r pyproject.toml
deactivate
unset https_proxy
unset http_proxy
echo "=== 虚拟环境准备完成 ==="
ENDSSH"""
                }
            }
        }

        stage('运行测试') {
            when {
                expression { env.CONNECTIVITY_FAILED != 'true' }
            }
            steps {
                script {
                    def apiKey = params.API_KEY?.toString()?.trim()
                    if (!apiKey) { apiKey = 'EMPTY' }
                    sshagent(credentials: ["${SSH_CREDENTIALS}"]) {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                            sh """
ssh -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} << 'ENDSSH'
set -e
cd ${params.WORK_DIR}
source .venv/bin/activate
mkdir -p ${BUILD_OUTPUT_DIR}

export KIMI_BASE_URL="${env.BASE_URL_V1}"
export KIMI_API_KEY="${apiKey}"
export MODEL_NAME="${params.MODEL}"
export KVV_SUITE="${params.SUITE}"
export KVV_THINK_MODE="${params.THINK_MODE}"
export KVV_THINKING="${params.THINKING}"
export KVV_PARALLEL_JOBS="${params.PARALLEL_JOBS}"
export KVV_RERUNS="${params.RERUNS}"
export KVV_RERUNS_DELAY="2"
export KVV_OUT_DIR="${params.WORK_DIR}/${BUILD_OUTPUT_DIR}"
export KVV_CASE_DIR="${params.CASE_DIR}"
export KVV_SELECTION="${params.SELECTION}"
export KVV_MAX_CASES="${params.MAX_CASES}"
export KVV_MAX_TOKENS="${params.MAX_TOKENS}"

bash run_kimivv_tests.sh
ENDSSH"""
                        }
                    }
                }
            }
        }

        stage('拉取报告到Jenkins') {
            when {
                expression { env.CONNECTIVITY_FAILED != 'true' }
            }
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    sshagent(credentials: ["${SSH_CREDENTIALS}"]) {
                        sh """
mkdir -p reports/${BUILD_NUMBER}
if ssh -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} "[ -d ${params.WORK_DIR}/${BUILD_OUTPUT_DIR} ]"; then
    ssh -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} "tar -czf /tmp/kvv_${BUILD_NUMBER}.tar.gz -C ${params.WORK_DIR}/${BUILD_OUTPUT_DIR} ."
    scp -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST}:/tmp/kvv_${BUILD_NUMBER}.tar.gz ./reports/${BUILD_NUMBER}/
    tar -xzf ./reports/${BUILD_NUMBER}/kvv_${BUILD_NUMBER}.tar.gz -C ./reports/${BUILD_NUMBER}/
    ssh -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} "rm -f /tmp/kvv_${BUILD_NUMBER}.tar.gz"
    echo "=== 拉取结果 ==="
    ls -la ./reports/${BUILD_NUMBER}/
else
    echo "警告: 远程输出目录 ${params.WORK_DIR}/${BUILD_OUTPUT_DIR} 不存在，跳过报告拉取"
fi
"""
                    }
                }
            }
        }

        stage('发送邮件') {
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    script {
                        def stats = [:]
                        def statsFile = "reports/${BUILD_NUMBER}/stats.properties"
                        if (fileExists(statsFile)) {
                            readFile(file: statsFile, encoding: 'UTF-8').split('\n').each { line ->
                                def matcher = line.trim() =~ /^([A-Za-z]+)=(.*)$/
                                if (matcher.matches()) {
                                    stats[matcher.group(1)] = matcher.group(2)
                                }
                            }
                        }

                        def reportFile = sh(script: "ls reports/${BUILD_NUMBER}/*_report.html 2>/dev/null | head -1", returnStdout: true).trim()
                        def logFile = sh(script: "ls reports/${BUILD_NUMBER}/*.log 2>/dev/null | head -1", returnStdout: true).trim()

                        def reportStyle = ""
                        def reportBody = ""
                        if (reportFile && fileExists(reportFile)) {
                            def content = readFile(file: reportFile, encoding: 'UTF-8')
                            def styleMatch = content =~ /(?s)<style>(.*?)<\/style>/
                            if (styleMatch.find()) {
                                reportStyle = styleMatch.group(1)
                            }
                            def bodyMatch = content =~ /(?s)<body>(.*?)<\/body>/
                            if (bodyMatch.find()) {
                                reportBody = bodyMatch.group(1)
                            }
                        }

                        def fallbackStyle = "body { font-family: 'Segoe UI', 'Microsoft YaHei', Arial, sans-serif; margin: 0 auto; max-width: 1080px; padding: 20px 32px; color: #1f2937; background: #eef1f6; font-size: 14px; line-height: 1.7; }\ntable { border-collapse: collapse; width: 100%; margin: 14px 0 18px; background: #ffffff; font-size: 13px; border: 1px solid #d8dfeb; }\nth { background: #e3edfc; color: #1e3a8a; font-weight: 600; }\nth, td { border: 1px solid #d8dfeb; padding: 6px 12px; text-align: left; vertical-align: top; }\n.email-footer { margin-top: 24px; padding: 12px 15px; background-color: #f9f9f9; border-radius: 3px; color: #666; font-size: 12px; }"

                        def connectivityFailed = (env.CONNECTIVITY_FAILED == 'true')
                        def resultLabel = connectivityFailed ? '连通性预检未通过' : (stats.result ?: (currentBuild.currentResult == 'SUCCESS' ? 'PASS' : 'FAIL'))
                        def rateLabel = stats.rate ? "${stats.rate}%" : 'N/A'
                        def bannerColor = (resultLabel == 'PASS') ? '#4CAF50' : '#f44336'

                        def emailBody = """
<html>
<head>
<meta charset="utf-8">
<style>
${reportStyle ?: fallbackStyle}
</style>
</head>
<body>
<div style="background-color: ${bannerColor}; color: white; padding: 18px 20px; border-radius: 5px 5px 0 0; margin-bottom: 10px;">
    <h2 style="margin: 0;">KVV 模型服务测试报告 - 构建 #${BUILD_NUMBER}</h2>
</div>
${connectivityFailed ? '<div style="background-color: #d32f2f; color: white; padding: 12px 15px; border-radius: 4px; margin-bottom: 12px;"><strong>API 连通性预检未通过</strong>：本次构建未执行测试，请检查模型服务是否可达（BASE_URL / API_KEY 是否正确），详见构建日志。</div>' : ''}
<h2>Jenkins 构建信息</h2>
<table>
    <tr><th>构建编号</th><td>#${BUILD_NUMBER}</td></tr>
    <tr><th>测试人员</th><td>${params.TESTER}</td></tr>
    <tr><th>模型服务描述</th><td>${params.DESCRIPTION ?: '(无)'}</td></tr>
    <tr><th>测试套件</th><td>${params.SUITE}</td></tr>
    <tr><th>模型名称</th><td>${params.MODEL}</td></tr>
    <tr><th>API 地址</th><td>${params.BASE_URL}</td></tr>
    <tr><th>ThinkMode</th><td>${params.THINK_MODE}</td></tr>
    <tr><th>Thinking</th><td>${params.THINKING}</td></tr>
    <tr><th>并发数</th><td>${params.PARALLEL_JOBS}</td></tr>
    <tr><th>重试次数</th><td>${params.RERUNS}</td></tr>
    <tr><th>用例总数</th><td>${stats.total ?: 'N/A'}</td></tr>
    <tr><th>通过 / 失败</th><td>${stats.passed ?: 'N/A'} / ${stats.failed ?: 'N/A'}</td></tr>
    <tr><th>跳过 / 错误</th><td>${stats.skipped ?: 'N/A'} / ${stats.errors ?: 'N/A'}</td></tr>
    <tr><th>通过率</th><td>${rateLabel}</td></tr>
    <tr><th>测试结果</th><td style="color: ${bannerColor}; font-weight: bold;">${resultLabel}</td></tr>
    <tr><th>执行时间</th><td>${currentBuild.durationString}</td></tr>
    <tr><th>构建状态</th><td>${currentBuild.currentResult}</td></tr>
</table>
${reportBody ?: '<p>未能生成 HTML 测试报告，请查看构建日志与附件。</p>'}
<p style="margin-top: 20px;">详细测试报告（与本地 generate_test_report.py 格式一致）与运行日志请查看附件。</p>
<p>Jenkins 构建地址: <a href="${env.BUILD_URL}">${env.BUILD_URL}</a></p>
<div class="email-footer">此邮件由 Jenkins 自动发送，请勿回复。</div>
</body>
</html>"""

                        def attachPatterns = []
                        if (reportFile && fileExists(reportFile)) {
                            attachPatterns.add("reports/${BUILD_NUMBER}/*_report.html")
                        }
                        if (logFile && fileExists(logFile)) {
                            attachPatterns.add("reports/${BUILD_NUMBER}/*.log")
                        }

                        emailext(
                            subject: "[KVV 测试报告] ${params.MODEL} - 套件 ${params.SUITE} - 构建 #${BUILD_NUMBER} - ${resultLabel}${rateLabel != 'N/A' ? " (通过率 ${rateLabel})" : ''}",
                            body: emailBody,
                            to: "${params.RECIPIENTS}",
                            mimeType: 'text/html',
                            attachmentsPattern: attachPatterns.join(',')
                        )
                    }
                }
            }
        }

        stage('清理旧构建') {
            steps {
                sshagent(credentials: ["${SSH_CREDENTIALS}"]) {
                    sh """
ssh -o StrictHostKeyChecking=no ${REMOTE_USER}@${REMOTE_HOST} << 'ENDSSH'
cd ${params.WORK_DIR}/builds/${params.TESTER} 2>/dev/null || exit 0
ls -t | tail -n +21 | xargs -r rm -rf
ENDSSH"""
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: "reports/${env.BUILD_NUMBER}/**", allowEmptyArchive: true, fingerprint: true
        }
        cleanup {
            cleanWs()
        }
    }
}

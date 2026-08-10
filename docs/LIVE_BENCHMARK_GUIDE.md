# Live LLM Benchmark Guide

> 目标：配置真实 LLM API Key，运行 `scripts/golden_eval.py --live`，拿到可写进简历的真实模型质量数据。

## 1. 推荐：DeepSeek（国内可访问，OpenAI 兼容）

1. 打开 <https://platform.deepseek.com/api_keys>
2. 注册并充值少量余额
3. 创建 API Key，复制以 `sk-` 开头的字符串
4. 不要把这个 Key 粘贴到聊天窗口，只写入本机 `.env`

其他已支持厂商见 `app/core/llm_providers.py`：阿里 DashScope、火山方舟、OpenAI、Google Gemini。

## 2. 编辑 `.env`

用任意编辑器打开项目根目录下的 `.env`，改成以下内容（把 `sk-...` 换成你的真实 Key）：

```dotenv
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-your-real-key-here
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=8192

CODE_LLM_BASE_URL=https://api.deepseek.com/v1
CODE_LLM_API_KEY=sk-your-real-key-here
CODE_LLM_MODEL=deepseek-chat
CODE_LLM_TEMPERATURE=0.2
CODE_LLM_MAX_TOKENS=8192

DEEPSEEK_API_KEY=sk-your-real-key-here
```

> 注意：现有 `.env` 里 `LLM_MAX_TOKENS=1024` 太低，代码生成会被截断；请至少改成 8192。

## 3. 验证是否退出 DryRun

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -c "from app.core.llm_adapter import llm; print('dry_run=', llm.dry_run, 'model=', llm.current_model, 'base=', llm.base_url)"
```

期望输出：

```text
dry_run= False model= deepseek-chat base= https://api.deepseek.com/v1
```

只要 `dry_run= True`，后续一定还是占位代码，不会调用真实模型。

## 4. 运行 Live Golden Eval

```powershell
.\.venv\Scripts\python.exe scripts\golden_eval.py --live --output-dir benchmark-results
```

它会依次运行 8 个 Golden 任务，每次调用真实 LLM 生成代码，再检查语法、符号、关键词和最小产物。耗时通常几分钟，具体取决于模型速度和限流。

退出码：

- `0`：`PASS` 或 `WARN`
- `1`：`BLOCK`

## 5. 查看结果

```powershell
Get-Content benchmark-results\golden-report.md
Get-Content benchmark-results\golden-report.json
```

简历里可写：

> 使用 DeepSeek `deepseek-chat` 跑 8 个 Golden 代码生成任务，8/8 通过。

当前仓库已保存一份 live 结果：`benchmark-results/golden-report.json` / `golden-report.md`。

## 6. 常见问题

### 仍是 dry_run=True

- Key 是否仍是 `sk-your-key-here` 占位符
- 是否保存 `.env` 后重新打开终端
- 是否在当前项目根目录运行命令

### 401 / 403 / 404

- Key 是否有效、余额是否足够
- `LLM_BASE_URL` 是否和厂商文档一致
- `LLM_MODEL` 是否写成了厂商不存在的模型名

### 提示 quota exhausted

如果充值后仍提示额度耗尽，删除运行时额度状态文件后重试：

```powershell
Remove-Item -Force runtime\.quota_state.json -ErrorAction SilentlyContinue
```

## 7. 可选：完整 Live Benchmark

Golden 只覆盖 8 个确定性任务；想拿更完整的延迟、Token、评审质量数据，再跑：

```powershell
.\.venv\Scripts\python.exe scripts\benchmark.py --live --output-dir benchmark-results
```

输出会自动写入 `mode=live`、模型名、命令和完成时间。

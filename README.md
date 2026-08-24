# agent-jupyter

面向 AI Agent 的无持久状态 Jupyter kernel CLI。计算状态完全保存在 kernel 中；每次 CLI 调用只连接、发送请求、收集响应并退出。

项目使用 Pixi 管理，开发环境固定为 Python 3.12.x，Python 包要求 `>=3.12`。正式支持 Linux 与 macOS，暂不支持 Windows。CLI 本身的 Python 版本不限制目标 kernel 的 Python 版本；兼容性取决于 Jupyter messaging protocol 和目标环境中的 kernel 实现。

## 开发

```bash
pixi install
pixi run test
```

### 构建本地 conda 包

使用独立的 Pixi build 环境通过 rattler-build 从项目根源码构建纯 Python
包；产物写入项目内的 `dist/conda`：

```bash
pixi run -e build build-conda
```

用户可以把项目目录作为本地 conda channel 安装（将路径替换为项目的绝对路径）：

```bash
pixi global install \
  --channel file:///absolute/path/to/agent-jupyter/dist/conda \
  --channel conda-forge \
  agent-jupyter
agent-jupyter install-skill
```

安装包不会自动写入用户目录；只有显式执行 `install-skill` 才会安装内置 Skill。

## 使用

安装内置 Agent Skill 文档是显式操作，不会在安装 Python 包时修改用户目录。默认写入跨 harness 通用的 `~/.agents/skills/agent-jupyter/SKILL.md`；目标文件已存在时默认拒绝覆盖，确认后可使用 `--force`：

```bash
agent-jupyter install-skill
agent-jupyter install-skill --skills-root /path/to/skills
agent-jupyter install-skill --skills-root /path/to/skills --force
```

`--root` 是 `--skills-root` 的别名。命令成功时在 stdout 输出写入的文件路径。

```bash
# 扫描标准 runtime directory；可重复提供额外目录
agent-jupyter list --runtime-dir /extra/runtime

# 查看 kernel 信息
agent-jupyter info --connection /path/to/kernel.json

# 启动独立 kernel，命令退出后 kernel 继续运行
agent-jupyter start --kernel-name python3 --json

# 三种代码来源互斥
agent-jupyter exec --connection /path/to/kernel.json -c 'print(1)'
agent-jupyter exec --connection /path/to/kernel.json --file script.py
cat script.py | agent-jupyter exec --connection /path/to/kernel.json

# 显式改变执行或生命周期
agent-jupyter interrupt --connection /path/to/kernel.json
agent-jupyter shutdown --connection /path/to/kernel.json
```

`--connection-file` 是 `--connection` 的兼容别名。工具不会自动选择“最近”的 kernel，也不会删除 stale connection file。

## 执行语义

执行请求默认使用 `allow_stdin=False`，因此代码调用 `input()` 时会由 kernel 返回执行错误。`exec` 按 execute `msg_id` 过滤消息，并等待对应的 shell `execute_reply` 与 IOPub `idle`。

连接超时由 `--connection-timeout`（默认 10 秒）控制；执行默认无限等待，可通过 `--execution-timeout` 显式设置上限。执行超时只断开当前 client，不会 interrupt 或 shutdown kernel，因此代码可能仍在运行。

普通文本模式输出可读的 stream、display/result 和错误。`--json` 向 stdout 写入一个 JSON 对象；CLI 诊断写入 stderr：

```json
{
  "status": "ok",
  "execution_count": 3,
  "outputs": [
    {"type": "stream", "name": "stdout", "text": "hello\n"}
  ]
}
```

`list`、`info` 以及 `start --json` 同样只向 stdout 输出 JSON。connection file 含有可执行任意代码的认证密钥，应作为敏感文件处理。

## 生命周期规则

- `start` 返回、`exec` 成功或失败、CLI 超时或退出，都不会关闭 kernel。
- `interrupt` 与 `shutdown` 只能显式调用。
- `shutdown` 关闭整个 kernel，可能影响 VS Code、JupyterLab 等其他客户端。
- `list` 只探测 connection file；无法连接时报告错误，不自动清理文件。

## 当前边界

暂不支持交互式 stdin、Jupyter Server API、VS Code 私有 API、透明重连、并发 execute、Windows，以及 CLI 自有 session/registry 数据库。

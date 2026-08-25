---
name: agent-jupyter
description: Use the agent-jupyter CLI to execute, interrupt, and shut down Jupyter kernels while preserving kernel state between calls.
---

# agent-jupyter

Use this skill to work with a Jupyter kernel while keeping its state between
short-lived CLI calls. The kernel outlives the CLI process. Use an explicit
connection-file path for deterministic, machine-friendly operations.

## Workflow

### 1. Reuse a connection that is already known

First check whether the conversation or the current command context already
contains a specific, trustworthy Jupyter connection-file path. If it does,
reuse that exact path for subsequent `exec` calls. Do not rediscover a kernel
or ask for the path again.

### 2. Obtain a path when none is known

If no path is present but the user intends to use an existing kernel, guide
them to obtain the connection file from that specifically intended kernel and
provide its path. A Python/IPython kernel can print it with:

```python
from IPython import get_connection_file
print(get_connection_file())
```

Do not infer or select the intended kernel from runtime-file inventories,
recency, metadata, summaries, or liveness checks. A connection path must come
from the intended existing kernel or from the start command below.

If the user wants a new independent kernel instead, start one and retain the
connection path returned by the command:

```bash
agent-jupyter start --kernel-name python3 --json
```

### 3. Execute against the same kernel

Use the retained explicit path for every related call so variables, imports,
files, and other state remain available:

```bash
agent-jupyter exec --connection /path/to/kernel.json --json -c 'answer = 40 + 2; answer'
```

For multiline code, use a quoted heredoc:

```bash
agent-jupyter exec --connection /path/to/kernel.json --json <<'PY'
import statistics
values = [1, 2, 3]
mean = statistics.mean(values)
print(mean)
PY
```

## Commands and safeguards

- Pass `--connection` (or its `--connection-file` alias) explicitly to
  `exec`, `interrupt`, `shutdown`, and `info`. Never rely on an implicit
  current or most-recent kernel.
- `info` is not part of the normal execution loop. Use it only as an
  exception-diagnostic tool after a connection or execution problem when
  kernel details are needed. Diagnostics must never silently select or switch
  to another kernel; continue using the explicit path or report the problem.
- Prefer `--json` when another tool or agent will parse output. Human-readable
  mode is suitable for quick inspection.
- Kernel stdin is disabled (`allow_stdin=False`). `input()` and other
  interactive stdin requests are unsupported; provide inputs in code or by
  another non-interactive mechanism.
- An execution timeout disconnects only the CLI client. It does not interrupt
  or shut down the kernel, and submitted code may still be running. Use
  `interrupt` explicitly when stopping execution is intended, then reconnect
  with the same connection file to inspect the result.
- Normal CLI exit, successful or failed execution, and timeout leave the
  kernel running. Only explicit `interrupt` and `shutdown` alter execution or
  kernel lifecycle. Use `shutdown` sparingly because it terminates the kernel
  for other clients too; confirm exclusive ownership first.
- Neither `interrupt` nor `shutdown` implicitly deletes connection files.
  Treat connection files as sensitive credentials because they contain
  authentication keys, and never print or expose their contents.

---
name: agent-jupyter
description: Use the agent-jupyter CLI to discover, inspect, execute, interrupt, and shut down Jupyter kernels while preserving kernel state between calls.
---

# agent-jupyter

Use this skill when you need to work with an existing Jupyter kernel through the
`agent-jupyter` CLI. The CLI process is short-lived; the kernel and its state
are not. Prefer machine-readable JSON and explicit connection files so that
commands are deterministic.

## Core workflow

1. Discover kernels when you do not already have a connection file:

   ```bash
   agent-jupyter list
   agent-jupyter list --runtime-dir /path/to/extra/runtime
   ```

   `list` returns responsive kernels, omits unreachable connection files, and
   never deletes them. Identified entries include compact `language` and
   `implementation` summaries when those string values are available; raw
   `kernel_info` is not included. A responsive entry can lack those summaries
   when identification times out. Use `info` on an explicit connection when
   the complete raw `kernel_info` response is needed. If `complete` is false,
   the discovery deadline produced partial
   results; rerun with a larger `--timeout` when a complete inventory is needed.

2. Inspect a selected kernel before using it:

   ```bash
   agent-jupyter info --connection /path/to/kernel.json
   ```

3. For a new independent kernel, start it and retain the returned connection
   path. JSON is preferable when capturing the path programmatically:

   ```bash
   agent-jupyter start --kernel-name python3 --json
   ```

4. Execute against that same connection file for every related call. This
   reuses variables, imports, files, and other state held by the kernel:

   ```bash
   agent-jupyter exec --connection /path/to/kernel.json --json -c 'answer = 40 + 2; answer'
   ```

   For multiline code, use a quoted heredoc rather than trying to put shell
   quoting around a large string:

   ```bash
   agent-jupyter exec --connection /path/to/kernel.json --json <<'PY'
   import statistics
   values = [1, 2, 3]
   mean = statistics.mean(values)
   print(mean)
   PY
   ```

## Execution rules

- Always pass `--connection` (the `--connection-file` alias is also accepted)
  for `info`, `exec`, `interrupt`, and `shutdown`. Do not rely on an implicit
  “current” or “most recent” kernel.
- Use `--json` when another tool or agent needs to parse a result. Human mode
  is useful for quick inspection, but JSON is the stable interface.
- `input()` and other kernel stdin requests are not supported: execution uses
  `allow_stdin=False`. Arrange all inputs in the code or pass data another
  way; do not wait for an interactive prompt.
- An execution timeout only disconnects the current CLI client. It does **not**
  stop, interrupt, or shut down the kernel, and the submitted code may still
  be running. Use `interrupt` deliberately when stopping execution is intended,
  then reconnect and inspect the result.
- A successful or failed `exec`, a CLI exit, and a CLI timeout all leave the
  kernel running. Kernel state can therefore be reused by subsequent `exec`
  calls with the same connection file.

## Lifecycle commands

Interrupt only when you explicitly intend to stop currently running code:

```bash
agent-jupyter interrupt --connection /path/to/kernel.json
```

Use `shutdown` sparingly. It terminates the entire kernel and can disrupt other
clients such as JupyterLab or VS Code; confirm that the kernel is exclusively
owned before running:

```bash
agent-jupyter shutdown --connection /path/to/kernel.json
```

Neither command implicitly cleans up connection files. Treat connection files
as sensitive because they contain authentication keys.

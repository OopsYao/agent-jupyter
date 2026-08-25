---
name: agent-jupyter
description: Use when asked to run code in a Jupyter, IPython, or notebook kernel; view, inspect, or display variable values or persistent state there; preserve variables across executions; connect through a kernel connection file; or start, inspect, interrupt, or shut down a kernel with agent-jupyter.
---

# agent-jupyter

Use `agent-jupyter` with an explicit Jupyter connection-file path.

## Safety model

A kernel is a persistent, potentially shared REPL—not an isolated script
runner.

- Variables, imports, working directory, background tasks, and other process
  state persist across executions.
- Code may have external side effects on files, databases, services, or remote
  systems.
- The user or another client may use the same kernel. Do not assume a clean
  namespace or exclusive ownership.
- Avoid unnecessary global-state changes, name collisions, namespace clearing,
  interrupts, and shutdowns.
- Prefer scoped names and idempotent operations. Explain destructive or
  surprising actions before running them.
- Connection files contain credentials. Use their paths, but never expose their
  contents.

Normal CLI exit, failure, and timeout leave the kernel running. A timeout only
disconnects the client; submitted code may still be running or may have
completed. Do not blindly retry code with side effects.

Kernel stdin is disabled, so `input()` is unsupported.

## Require an explicit connection

Use an existing kernel only when the user explicitly provides its literal
connection-file path or an earlier `agent-jupyter start` returned that path.
Reuse exactly that path; references such as "the current kernel" or "this
notebook" do not identify a connection. Never use `glob`, `ls`, `find`, or scan
runtime directories to search for, verify, or guess a connection file.

Without an explicit path, do not run any Jupyter command. Ask whether the user
wants to provide an existing kernel's exact connection-file path or create a
new kernel. In the same message, include this snippet for the user to run inside
an existing kernel:

```python
from ipykernel import get_connection_file
print(get_connection_file())
```

Then stop and wait for the user's response. Do not show a kernel-creation
command while asking. Never infer or silently select a connection path.

## Create a kernel

Enter this section only after the user explicitly asks you to create a new
kernel. Run the following command yourself; never quote, show, recommend, or
instruct the user to run it:

```bash
agent-jupyter start --kernel-name python3 --json
```

Reuse the returned path for related operations.

## Operate the kernel

Use the same explicit path for every related operation:

```bash
agent-jupyter exec --connection /path/to/kernel.json --json -c '40 + 2'
```

For multiline code:

```bash
agent-jupyter exec --connection /path/to/kernel.json --json <<'PY'
import statistics
values = [1, 2, 3]
print(statistics.mean(values))
PY
```

`info` is not part of the normal execution loop. Use it only after a connection
or execution problem when details about the known kernel are needed:

```bash
agent-jupyter info --connection /path/to/kernel.json --json
```

Diagnostics must not select, switch to, or start another kernel.

Interrupt only when stopping execution is intended:

```bash
agent-jupyter interrupt --connection /path/to/kernel.json --json
```

Shutdown affects every client and discards kernel state. Use it only when
termination is explicitly intended and shared ownership has been considered:

```bash
agent-jupyter shutdown --connection /path/to/kernel.json --json
```

Only explicit `interrupt` and `shutdown` alter execution or lifecycle. Neither
command deletes connection files; never delete stale connection files
automatically.

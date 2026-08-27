---
name: agent-jupyter
description: Use whenever the user intends to execute code, inspect persistent state, interrupt, shut down, or create a Jupyter, IPython, or notebook kernel, including requests that refer only to an existing, current, or running kernel without providing connection details.
---

# agent-jupyter

## Required operation handle

**Do not find or select a kernel. `agent-jupyter` operates on a literal Jupyter
connection-file path.**

A kernel, notebook, kernel name, kernelspec, process ID, server URL, or phrase
such as "the current kernel" is not an operation target. Do not resolve any of
these into a connection file.

A usable connection-file path may come from exactly two sources:

1. The user explicitly supplied the literal path.
2. `agent-jupyter start` returned the path after the user explicitly requested
   creation of a new kernel.

Pass that exact path to every subsequent operation. Never infer, search for,
verify, rank, or silently replace it. Do not use:

- `jupyter kernelspec list` or similar Jupyter discovery commands
- `agent-jupyter list`
- `glob`, `ls`, `find`, or runtime-directory scanning
- process inspection, notebook metadata, server APIs, or editor APIs

A kernelspec such as `python3` is only a definition used to create a new kernel.
It does not identify a running kernel and cannot replace a connection-file path.

## When no connection path is available

If neither allowed source has supplied a literal path:

1. Do not run any Jupyter or filesystem discovery command.
2. Ask whether the user wants to provide an existing connection-file path or
   create a new kernel.
3. For an existing connection, include this snippet for the user to run inside
   that kernel:

   ```python
   from ipykernel import get_connection_file
   print(get_connection_file())
   ```

4. Stop and wait for the response.

Do not show or run a creation command while asking. Do not continue investigating
how to locate the kernel, and never infer or silently select a connection path.

## Create a new connection

Enter this section only after the user explicitly requests creation of a new
kernel. Run the following command yourself; never quote, show, recommend, or
instruct the user to run it:

```bash
agent-jupyter start --kernel-name python3 --json
```

Treat the returned `connection` path as the operation handle. Do not choose
another kernelspec unless the user requests one.

## Use the connection

Use the same literal path for every related operation:

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

Use `info` only to diagnose a connection or execution problem involving the
already-known path:

```bash
agent-jupyter info --connection /path/to/kernel.json --json
```

Diagnostics must not discover, select, or create another connection.

Interrupt only when stopping execution is intended:

```bash
agent-jupyter interrupt --connection /path/to/kernel.json --json
```

Shutdown affects every client and discards kernel state:

```bash
agent-jupyter shutdown --connection /path/to/kernel.json --json
```

Use it only when termination is explicitly intended and shared ownership has
been considered.

## Safety model

The connection file grants access to a persistent, potentially shared REPL.

- Connection files contain credentials. Use their paths, but never expose or
  read out their contents.
- Variables, imports, working directory, background tasks, and other process
  state persist across executions.
- Code may have external side effects on files, databases, services, or remote
  systems.
- The user or another client may use the same kernel. Do not assume a clean
  namespace or exclusive ownership.
- Prefer scoped names and idempotent operations. Avoid unnecessary global-state
  changes, name collisions, namespace clearing, interrupts, and shutdowns.
- Explain destructive or surprising actions before running them.
- Kernel stdin is disabled, so `input()` is unsupported.

Normal CLI exit, execution failure, and timeout leave the kernel running. A
timeout disconnects only this client; submitted code may still be running or
may have completed. Do not blindly retry code with side effects.

Only explicit `interrupt` and `shutdown` alter execution or lifecycle. Neither
command deletes connection files; never delete stale connection files
automatically.

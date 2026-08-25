# AGENTS.md

## Project Model

`agent-jupyter` is a short-lived CLI for discovering and controlling Jupyter kernels. Keep computation state in the kernel; do not add a CLI session registry, database, or implicit “current kernel.”

Use explicit Jupyter connection files. Preserve these lifecycle semantics:

- Normal CLI exit, successful execution, execution failure, and timeout leave the kernel running.
- Execution timeout disconnects the client; it does not imply interrupt or shutdown.
- Only explicit `interrupt` and `shutdown` commands alter kernel execution or lifecycle.
- Never delete stale connection files automatically.
- Keep kernel stdin disabled; interactive `input()` is outside the supported scope.

Discover kernels in layers:

1. Scan connection files and reject malformed or clearly unusable candidates from file metadata.
2. Rank the remaining candidates by inexpensive signals such as recency; treat these signals as hints, not proof of liveness.
3. Probe candidate liveness with a short, bounded heartbeat check. Probe concurrently with a fixed concurrency limit and an overall discovery deadline.
4. Request `kernel_info` only from responsive candidates so agents can choose by language and implementation.
5. Return every usable candidate found within the deadline, including responsive candidates whose `kernel_info` timed out; do not stop after the first result.
6. Omit unreachable candidates from normal output. Do not add an exhaustive stale-file reporting mode unless explicitly requested.
7. Return partial results when the overall deadline expires and mark the discovery result as incomplete.
8. Distinguish responsive candidates from fully identified candidates; do not infer that a timed-out candidate is stale or delete its file.

Do not use full client readiness waits for bulk discovery. Keep `info` as the complete check for one explicitly selected connection.

## Routing

| Change | Start here | Verify with |
| --- | --- | --- |
| CLI commands, arguments, exit codes, stdout/stderr behavior | `src/agent_jupyter/cli.py` | CLI tests through `main([...])` in `tests/test_skill.py`; add a focused CLI test when no precedent exists |
| Execute requests, message filtering, timeout, output normalization | `src/agent_jupyter/execution.py` | `tests/test_execution.py` |
| Kernel discovery, info, start, interrupt, shutdown | `src/agent_jupyter/lifecycle.py` | `tests/test_lifecycle.py` |
| Skill installation and overwrite behavior | `src/agent_jupyter/skill.py` | `tests/test_skill.py` |
| Agent-facing workflow instructions | `src/agent_jupyter/resources/agent_jupyter_skill.md` | `tests/test_skill.py` and the corresponding CLI help |
| Python metadata, entry point, packaged resources | `pyproject.toml` | build the wheel or conda package and inspect/import the installed resource |
| Pixi environments and tasks | `pixi.toml` | run the affected Pixi environment/task |
| Conda package contents and smoke tests | `recipe/recipe.yaml` | `pixi run -e build build-conda` |

Stop exploring when the affected files and lifecycle/protocol invariants are enumerated. Read the nearest test before changing its source module.

## Environment

- Manage development and verification with Pixi.
- Use Python 3.12.x for the development environment; the package requires Python 3.12 or newer.
- Support Linux and macOS: `linux-64`, `linux-aarch64`, `osx-64`, and `osx-arm64`.
- Do not claim Windows support.
- Keep the CLI runtime independent from the target kernel's Python version; interoperability is through the Jupyter messaging protocol.

## Verification

Install or update the declared environment before verification:

```bash
pixi install
```

Run the complete automated suite after source, resource, or test changes:

```bash
pixi run test
```

No lint, formatter, or static-type command is declared. Do not invent one as a project gate.

Do not test Skill prose with literal substring assertions. Test installation,
packaging, and executable behavior instead.

After packaging changes, build the conda package and require its recipe tests to pass:

```bash
pixi run -e build build-conda
```

For installation-path changes, install from `dist/conda` using an isolated temporary `PIXI_HOME`; never mutate the user's global Pixi installation during verification.

## Packaging and Skill Distribution

Build the local conda channel under `dist/conda`; treat `dist/`, `build/`, `*.egg-info/`, `.pixi/`, and cache directories as generated artifacts, not source.

Keep the Skill source packaged as a regular resource. Installation of the Python/conda package must not write into the user's home directory. Expand the standard layout only after an explicit command:

```text
<skills-root>/agent-jupyter/SKILL.md
```

Default to `~/.agents/skills`; refuse to overwrite an existing Skill unless the user passes `--force`.

## Safety Boundaries

- Treat connection files as credentials; never print their HMAC keys or embed their contents in diagnostics.
- Require an explicit connection for `info`, `exec`, `interrupt`, and `shutdown`; do not select the newest kernel implicitly.
- Keep stdout machine-readable for JSON modes and route CLI diagnostics to stderr.
- Do not add Jupyter Server APIs, VS Code private APIs, transparent reconnect, concurrent execute, or interactive stdin without a new explicit design decision.
- Do not use package-install hooks that mutate user directories.

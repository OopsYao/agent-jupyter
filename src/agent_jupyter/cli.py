"""Command-line interface for agent-jupyter."""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from .execution import (
    AgentJupyterError,
    ExecutionTimeoutError,
    KernelExecutor,
    output_text,
)
from .lifecycle import (
    discover_kernels,
    interrupt_kernel,
    kernel_info,
    json_text,
    shutdown_kernel,
    start_kernel,
)
from .skill import SkillInstallError, install_skill, package_skill


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def _add_connection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--connection",
        "--connection-file",
        dest="connection",
        required=True,
        metavar="PATH",
        help="explicit Jupyter kernel connection file",
    )


def _add_timeout(parser: argparse.ArgumentParser, name: str, default: float, help_text: str) -> None:
    parser.add_argument(
        name,
        type=_positive_float,
        default=default,
        metavar="SECONDS",
        help=f"{help_text} (default: {default:g})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-jupyter",
        description="Discover, inspect, execute, and explicitly manage Jupyter kernels.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    install_parser = commands.add_parser(
        "install-skill", help="install the built-in Agent Skill document"
    )
    install_parser.add_argument(
        "--skills-root",
        "--root",
        dest="skills_root",
        metavar="PATH",
        help="skills directory (default: ~/.agents/skills)",
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing agent-jupyter/SKILL.md",
    )

    package_parser = commands.add_parser(
        "package-skill", help="package the built-in Agent Skill as a ZIP archive"
    )
    package_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="PATH",
        help="output ZIP path",
    )
    package_parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output archive",
    )

    list_parser = commands.add_parser("list", help="list runtime connection files")
    list_parser.add_argument(
        "--runtime-dir",
        action="append",
        default=[],
        metavar="PATH",
        help="additional runtime directory to scan (repeatable)",
    )
    _add_timeout(list_parser, "--probe-timeout", 1.0, "per-kernel probe timeout")
    _add_timeout(list_parser, "--timeout", 3.0, "overall discovery timeout")

    info_parser = commands.add_parser("info", help="return kernel_info for a kernel")
    _add_connection(info_parser)
    _add_timeout(info_parser, "--connection-timeout", 10.0, "kernel readiness timeout")

    exec_parser = commands.add_parser("exec", help="execute code in a kernel")
    _add_connection(exec_parser)
    source = exec_parser.add_mutually_exclusive_group()
    source.add_argument("-c", dest="code", metavar="CODE", help="execute CODE")
    source.add_argument("--file", type=Path, metavar="PATH", help="execute a source file")
    source.add_argument(
        "--stdin",
        action="store_true",
        help="read code from stdin (the default when no source option is given)",
    )
    exec_parser.add_argument(
        "--json",
        action="store_true",
        help="write one JSON result object to stdout",
    )
    _add_timeout(exec_parser, "--connection-timeout", 10.0, "kernel readiness timeout")
    _add_timeout(
        exec_parser,
        "--execution-timeout",
        math.inf,
        "execution and idle-wait timeout",
    )

    start_parser = commands.add_parser("start", help="start an independent kernel")
    start_parser.add_argument(
        "kernel_name_arg",
        nargs="?",
        metavar="KERNEL_NAME",
        help="kernelspec name (default: python3)",
    )
    start_parser.add_argument(
        "--kernel-name",
        "--kernelspec",
        dest="kernel_name_option",
        metavar="NAME",
        help="kernelspec name (default: python3)",
    )
    start_parser.add_argument(
        "--runtime-dir",
        metavar="PATH",
        help="runtime directory in which to write the connection file",
    )
    start_parser.add_argument(
        "--json",
        action="store_true",
        help="write the connection file as a JSON object",
    )
    _add_timeout(start_parser, "--startup-timeout", 60.0, "kernel readiness timeout")

    for name, help_text in (
        ("interrupt", "send an explicit interrupt request"),
        ("shutdown", "send an explicit shutdown request"),
    ):
        lifecycle_parser = commands.add_parser(name, help=help_text)
        _add_connection(lifecycle_parser)

    return parser


def _read_code(args: argparse.Namespace) -> str:
    if args.code is not None:
        return args.code
    if args.file is not None:
        return args.file.read_text(encoding="utf-8")
    return sys.stdin.read()


def _write_human(result: object) -> None:
    # Keep successful kernel results on stdout. Kernel errors are diagnostics
    # for human mode and therefore go to stderr.
    for output in result.outputs:  # type: ignore[union-attr]
        rendered = output_text(output)
        if output["type"] == "error":
            stream = sys.stderr
        else:
            stream = sys.stdout
        stream.write(rendered)
        if output["type"] != "stream" and not rendered.endswith("\n"):
            stream.write("\n")


def _write_json(value: object) -> None:
    sys.stdout.write(json_text(value))
    sys.stdout.write("\n")


def _start_name(args: argparse.Namespace) -> str:
    return args.kernel_name_option or args.kernel_name_arg or "python3"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "install-skill":
            print(install_skill(args.skills_root, force=args.force))
            return 0

        if args.command == "package-skill":
            print(package_skill(args.output, force=args.force))
            return 0

        if args.command == "list":
            _write_json(
                discover_kernels(
                    args.runtime_dir,
                    timeout=args.probe_timeout,
                    discovery_timeout=args.timeout,
                )
            )
            return 0

        if args.command == "info":
            _write_json(kernel_info(args.connection, timeout=args.connection_timeout))
            return 0

        if args.command == "exec":
            try:
                code = _read_code(args)
            except OSError as exc:
                print(f"agent-jupyter: could not read source: {exc}", file=sys.stderr)
                return 2
            result = KernelExecutor(
                args.connection,
                connection_timeout=args.connection_timeout,
                execution_timeout=args.execution_timeout,
            ).execute(code, allow_stdin=False)
            if args.json:
                _write_json(result.as_dict())
            else:
                _write_human(result)
            return 0 if result.status == "ok" else 1

        if args.command == "start":
            connection = start_kernel(
                _start_name(args),
                startup_timeout=args.startup_timeout,
                runtime_dir=args.runtime_dir,
            )
            if args.json:
                _write_json({"connection": connection})
            else:
                print(connection)
            return 0

        if args.command == "interrupt":
            interrupt_kernel(args.connection)
            return 0

        if args.command == "shutdown":
            shutdown_kernel(args.connection)
            return 0

        parser.error(f"unknown command: {args.command}")
    except (AgentJupyterError, SkillInstallError) as exc:
        print(f"agent-jupyter: {exc}", file=sys.stderr)
        return 124 if isinstance(exc, ExecutionTimeoutError) else 2
    except Exception as exc:  # unexpected client/library failure
        print(f"agent-jupyter: unexpected failure: {exc}", file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

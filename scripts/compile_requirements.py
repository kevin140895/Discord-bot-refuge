from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
LOCKS = (
    ("requirements.in", "requirements.txt"),
    ("requirements-dev.in", "requirements-dev.txt"),
)


def _compile(source: str, output: str, extra_args: list[str]) -> None:
    env = os.environ.copy()
    env["CUSTOM_COMPILE_COMMAND"] = "python scripts/compile_requirements.py"
    command = [
        sys.executable,
        "-m",
        "piptools",
        "compile",
        "--generate-hashes",
        "--resolver=backtracking",
        "--strip-extras",
        "--newline=lf",
        "--output-file",
        output,
        *extra_args,
        source,
    ]
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def main() -> int:
    if importlib.util.find_spec("piptools") is None:
        print(
            "pip-tools is required; install pip==26.1.2 and pip-tools==7.6.0 first.",
            file=sys.stderr,
        )
        return 2

    extra_args = sys.argv[1:]
    for source, output in LOCKS:
        _compile(source, output, extra_args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

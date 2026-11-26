"""Run the Flagsmith CLI (v2) and parse its --json output. Shared by apply.py and parity.py."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def binary():
    if os.environ.get("FLAGSMITH_CLI"):
        return os.environ["FLAGSMITH_CLI"]
    found = shutil.which("flagsmith")
    if not found:
        sys.exit("flagsmith CLI not found: npm install -g @flagsmith/cli")
    if os.name == "nt":
        # The npm shim is a .cmd that re-quotes arguments through cmd.exe; call the real exe instead.
        for exe in Path(found).parent.glob("node_modules/@flagsmith/cli/node_modules/@flagsmith/cli_windows_*/flagsmith.exe"):
            return str(exe)
    return found


BIN = None


def fs(*args, env_key=None, check=True):
    """flagsmith <args> --json --no-input; returns parsed JSON (or None for empty output)."""
    global BIN
    BIN = BIN or binary()
    cmd = [BIN, *map(str, args), "--json", "--no-input"]
    if env_key:
        cmd += ["-e", env_key]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        if not check:
            return None
        sys.exit(f"flagsmith {' '.join(map(str, args))} failed: {p.stderr.strip().splitlines()[0] if p.stderr.strip() else p.returncode}")
    return json.loads(p.stdout) if p.stdout.strip() else None

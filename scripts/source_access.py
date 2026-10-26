"""Read-only private source access using the runner's dedicated SSH key."""
import os
from pathlib import Path
import shlex

SOURCE_URL = "ssh://smarttang@git.nexusx.dev/nyra/nyra-lang.git"


def source_environment():
    if os.environ.get("NYRA_CI_RUNNER_DIRECTORY"):
        directory = Path(os.environ["NYRA_CI_RUNNER_DIRECTORY"]).expanduser() / "keys"
    elif os.environ.get("RUNNER_WORKSPACE"):
        directory = Path(os.environ["RUNNER_WORKSPACE"]).parents[1] / "keys"
    else:
        raise RuntimeError("set NYRA_CI_RUNNER_DIRECTORY to the configured runner for local release planning")
    key = directory / "source_ed25519"
    hosts = directory / "known_hosts"
    if not key.is_file() or not hosts.is_file():
        raise RuntimeError("runner source-read key or pinned host key is missing")
    ssh = (Path(os.environ["SystemRoot"]) / "System32/OpenSSH/ssh.exe").as_posix() if os.name == "nt" else "ssh"
    command = [ssh, "-i", key.as_posix(), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + hosts.as_posix()]
    return os.environ | {"GIT_SSH_COMMAND": shlex.join(command), "GIT_TERMINAL_PROMPT": "0"}

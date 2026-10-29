"""Check runner infrastructure without building or running compiler suites."""
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import urllib.parse
import subprocess
import sys
import tempfile
from source_access import SOURCE_URL, source_environment

report = {"system": platform.system(), "machine": platform.machine(),
          "execution_mode": os.environ.get("NYRA_CI_EXECUTION_MODE", "native"),
          "python": sys.version, "python_executable": sys.executable, "checks": []}


def run(command, env=None):
    result = subprocess.run(command, text=True, capture_output=True, timeout=45, env=env)
    report["checks"].append({"tool": command[0], "ok": result.returncode == 0,
                             "output": (result.stdout + result.stderr).strip()[:2000]})


try:
    report["source_addresses"] = sorted({str(r[4][0]) for r in socket.getaddrinfo("git.nexusx.dev", 80, type=socket.SOCK_STREAM)})
    report["proxy_endpoints"] = {k: urllib.parse.urlsplit(v).hostname for k, v in os.environ.items()
                                if k.lower() in ("http_proxy", "https_proxy", "all_proxy")}
    from runner_inputs import verify_providers
    machine = "arm64" if platform.machine().lower() in ("aarch64", "arm64") else "x86_64"
    report["providers"] = verify_providers(platform.system().lower() + "-" + machine)
    run(["git", "--version"])
    run(["git", "ls-remote", SOURCE_URL, "refs/heads/main"], source_environment())
    if platform.system() == "Darwin":
        run(["xcrun", "--show-sdk-path"])
        translated = subprocess.run(["sysctl", "-in", "sysctl.proc_translated"], capture_output=True, text=True)
        report["rosetta"] = translated.stdout.strip() == "1"
        expected = "x86_64" if os.environ.get("RUNNER_ARCH") == "X64" else "arm64"
        report["checks"].append({"tool": "Python architecture", "ok": platform.machine() == expected, "output": platform.machine()})
        run(["clang", "--version"])
    elif platform.system() == "Linux":
        run(["gcc", "--version"])
        run(["g++", "--version"])
        run(["clang", "--version"])
        run(["file", "--version"])
        run(["readelf", "--version"])
        run(["ldd", "--version"])
        run(["riscv64-linux-gnu-gcc", "-print-sysroot"])
        run(["pkg-config", "--modversion", "zlib", "libxml-2.0", "liblzma", "libzstd"])
    else:
        run(["clang", "--version"])
        endings = subprocess.run(["git", "config", "--type=bool", "--default", "false",
                                   "--get", "core.autocrlf"], capture_output=True,
                                  text=True, timeout=30)
        report["checks"].append({"tool": "Exact Git source bytes",
                                 "ok": endings.returncode == 0 and endings.stdout.strip() == "false",
                                 "output": "core.autocrlf=" + endings.stdout.strip()})
        # Run under the actual service token: interactive administrator access
        # does not prove that the runner can execute stdlib symbolic-link tests.
        with tempfile.TemporaryDirectory(prefix="nyra-runner-links-") as directory:
            root = Path(directory)
            target = root / "target.txt"
            target.write_text("runner symbolic-link probe")
            link = root / "file-link"
            link.symlink_to(target)
            if link.read_text() != target.read_text():
                raise RuntimeError("file symbolic-link read failed")
            target.unlink()
            if not link.is_symlink() or link.exists():
                raise RuntimeError("dangling symbolic-link detection failed")
            link.unlink()
            folder = root / "directory"
            folder.mkdir()
            link = root / "directory-link"
            link.symlink_to(folder, target_is_directory=True)
            moved = root / "renamed-directory-link"
            link.rename(moved)
            if not moved.is_dir() or not moved.is_symlink():
                raise RuntimeError("directory symbolic-link rename failed")
            moved.unlink()
        report["checks"].append({"tool": "Service symbolic links", "ok": True,
                                 "output": "file, dangling, directory and rename probes passed"})
        locator = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
        result = subprocess.run([str(locator), "-latest", "-products", "*", "-requires",
                                 "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                                capture_output=True, text=True, timeout=30)
        report["checks"].append({"tool": "Visual Studio C++", "ok": result.returncode == 0 and bool(result.stdout.strip()),
                                 "output": result.stdout.strip()})
except Exception as error:
    report["checks"].append({"tool": "infrastructure", "ok": False, "output": str(error)})
finally:
    report["ready"] = bool(report["checks"]) and all(row["ok"] for row in report["checks"])
    Path("health.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
if not report["ready"]:
    raise SystemExit(1)

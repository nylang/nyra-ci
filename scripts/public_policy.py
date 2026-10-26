"""Public SDK v1 file-shape policy, shared with the private exporter."""
from pathlib import PurePosixPath as Path
from release_common import safe_name as relative_file
PACKAGES = ("kernel", "ntest", "stdlib")
TOOLS = ("clang", "opt", "llc", "llvm-as", "llvm-dis", "llvm-link", "llvm-ar",
         "llvm-nm", "llvm-objdump", "llvm-readobj", "llvm-symbolizer", "llvm-dwarfdump")
FORBIDDEN_SUFFIXES = (".pdb", ".dSYM", ".ll", ".bc", ".log", ".profraw", ".profdata", ".pyc")
def executable_name(name, host):
    return name + ".exe" if host.startswith("windows-") else name

def _payload_allowed(name: str, host: str, targets: set[str]) -> bool:
    """Admit known product shapes, never a broad copy of an artifact directory."""
    path = relative_file(name)
    parts = path.parts
    if any(part.startswith(".") or part.lower().endswith(".dsym") for part in parts) or name.lower().endswith(tuple(suffix.lower() for suffix in FORBIDDEN_SUFFIXES)):
        return False
    if name in {f"hosts/{host}/bin/{executable_name(tool, host)}" for tool in ("nyrac", "nyra-analyzer")}:
        return True
    if name == f"hosts/{host}/share/nyra/analyzer/analysis_options.yaml":
        return True
    if len(parts) == 3 and parts[0] == "targets" and parts[1] in targets and parts[2] == "provider-requirements.json":
        return True
    llvm_prefix = f"hosts/{host}/llvm/"
    if name.startswith(llvm_prefix):
        relative = name[len(llvm_prefix):]
        if relative in ("llvm-manifest.json", "provenance.json", "sbom.cdx.json", "lib/static-dependencies.json"):
            return True
        native = {*TOOLS, "llvm-config", "llvm-config.bin", "lld-link", "llvm-lib", "ld64.lld", "ld.lld"}
        if relative in {"bin/" + executable_name(tool, host) for tool in native}:
            return True
        if relative == "bin/llvm-config":
            return True
        if relative.startswith(("include/llvm/", "include/llvm-c/", "lib/clang/", "licenses/")):
            # These are the already inventoried upstream LLVM developer payload,
            # not Nyra implementation packages. Reject Nyra/Python source there.
            return path.suffix not in (".ny", ".nk", ".ni", ".py", ".go")
        return len(relative.split("/")) == 2 and relative.startswith("lib/") and path.suffix in (".a", ".lib", ".dylib", ".so", ".dll")
    if len(parts) >= 4 and parts[0] == "targets" and parts[1] in targets and parts[2] in PACKAGES:
        relative = Path(*parts[3:]).as_posix()
        if relative in ("manifest.yaml", "package.yaml", "core-package.json", "lib/libpackage.a", "LICENSE", "LICENSE.txt", "NOTICE"):
            return True
        if parts[2] == "stdlib" and relative == "lib/libnyra_platform.a":
            return True
        if len(parts) == 5 and parts[3] in ("faces", "contracts") and path.suffix == ".json":
            return True
        if len(parts) == 5 and parts[3] == "objects" and path.suffix in (".o", ".obj"):
            return True
        if relative == f"sources/{parts[2]}.ny":
            return True
        return len(parts) >= 6 and parts[3] == "sources" and parts[4] in ("interface", "public") and path.suffix in (".ni", ".ny", ".nk") and "_selftest" not in path.name
    if name.startswith("frameworks/NyraRuntime.xcframework/"):
        return path.name == "Info.plist" or path.name == "libnyra_runtime.a" or name.endswith("/Headers/nyra_platform.h")
    return False

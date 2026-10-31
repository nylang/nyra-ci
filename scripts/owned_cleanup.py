"""Remove an already-authorized CI tree, including Windows read-only Git objects."""
from pathlib import Path
import shutil
import stat


def remove_tree(root):
    root = Path(root)
    if root.is_symlink() or root.is_junction():
        raise ValueError('refusing cleanup through a directory link')
    boundary = root.resolve()

    def readonly_retry(function, name, error):
        file = Path(name)
        if (not isinstance(error, PermissionError) or file.is_symlink()
                or not file.resolve().is_relative_to(boundary) or not file.is_file()):
            raise error
        mode = file.stat().st_mode
        if mode & stat.S_IWUSR:
            raise error
        # Git pack/index files are intentionally read-only on Windows. Change
        # only this owned regular file; do not relax directory ACLs or retry
        # unrelated access-denied failures.
        file.chmod(mode | stat.S_IWUSR)
        function(name)

    shutil.rmtree(root, onexc=readonly_retry)

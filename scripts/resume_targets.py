"""Admit only a reviewed complete profile prefix inside one owned target workset."""
import json
from pathlib import Path
import re
import subprocess
from release_common import safe_name,sha


def admit(work, host, plan, pin):
    if not re.fullmatch(r'[0-9]+-[0-9]+-' + re.escape(host), pin['workspace']) or work.name != pin['workspace']:
        raise ValueError('invalid owned continuation workset')
    owner=json.loads((work/'owner.json').read_text())
    if owner['owner'] != 'retained '+plan['version']+' target qualification':
        raise ValueError('continuation owner differs')
    file=work/'runtimes/completed-profiles.json'
    if sha(file)!=pin['checkpoint_sha256']:
        raise ValueError('reviewed completed-profile checkpoint changed')
    value=json.loads(file.read_text())
    if (value['host'],value['source_commit'],value['control_commit'])!=(host,plan['source_commit'],pin['control_commit']):
        raise ValueError('completed-profile source or controller differs')
    source=work/'source'
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()!=plan['source_commit'] or subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=source,text=True).strip():
        raise ValueError('continuation source is not the clean frozen checkout')
    for section in ('files','consumer_files'):
        for name,digest in value[section].items():
            path=work/'runtimes'/safe_name(name)
            if path.is_symlink() or not path.resolve().is_relative_to(work.resolve()) or sha(path)!=digest:
                raise ValueError('completed-profile payload/consumer changed')
    from release_common import policy
    keys=[r['key'] for r in value['targets']]
    if keys!=policy()['runtime_producers'][host][:len(keys)] or not keys:
        raise ValueError('completed-profile inventory is not a complete ordered prefix')
    for row in value['targets']:
        if row['status']!='passed' or row['acceptance_level']!=policy()['execution_required'].get(row['key'],'compile-only'):
            raise ValueError('partial profile cannot be reused')
    return value

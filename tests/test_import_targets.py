from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from import_targets import verify_catalog
from release_common import write,sha
class ImportTargetsTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.file=self.root/'targets/native/kernel/archive.a';self.file.parent.mkdir(parents=True);self.file.write_bytes(b'accepted bytes');self.plan={'version':'1.0.5','source_commit':'a'*40};self.v=dict(self.plan,host='host',control_commit='b'*40,targets=[dict(key='native',status='passed',acceptance_level='native-execution')],files={self.file.relative_to(self.root).as_posix():sha(self.file)},host_compilation=[]);self.save();self.p=patch('import_targets.policy',return_value={'runtime_producers':{'host':['native']},'execution_required':{'native':'native-execution'},'compile_only_hosts':{}});self.p.start();self.addCleanup(self.p.stop)
 def save(self):
  write(self.root/'catalog.json',self.v);self.pin={'catalog_sha256':sha(self.root/'catalog.json'),'control_commit':'b'*40}
 def test_exact_completed_payload_is_admitted(self):
  self.assertEqual(verify_catalog(self.root,'host',self.plan,self.pin),self.v)
 def test_changed_payload_is_rejected(self):
  self.file.write_bytes(b'substituted')
  with self.assertRaises(ValueError):verify_catalog(self.root,'host',self.plan,self.pin)
 def test_partial_or_wrong_control_is_rejected(self):
  self.v['targets'][0]['status']='failed';self.save()
  with self.assertRaises(ValueError):verify_catalog(self.root,'host',self.plan,self.pin)
  self.v['targets'][0]['status']='passed';self.v['control_commit']='c'*40;self.save()
  with self.assertRaises(ValueError):verify_catalog(self.root,'host',self.plan,self.pin)
if __name__=='__main__':unittest.main()

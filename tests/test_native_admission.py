"""SDK admission must retain complete gates and exact bytes, never a success flag alone."""
import copy,hashlib,json
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from admit_native import verify_complete

class Admission(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.compiler='';self.seed='f'*64
  self.plan=dict(version='1.0.5',source_commit='a'*40);self.manifest=dict(format='nyra-sdk-v2',version='1.0.5',host='linux-x86_64',source_revision='a'*40,acceptance={k:True for k in ('clean_source','fixed_point','full_regression','full_unit')},tools={},build_evidence='share/nyra/build-evidence.json')
  for name in ('nyrac','nyra-analyzer','nyra'):
   file=self.root/'bin'/name;file.parent.mkdir(exist_ok=True);file.write_bytes(name.encode());self.manifest['tools'][name]=dict(path='bin/'+name,version='1.0.5',sha256=hashlib.sha256(file.read_bytes()).hexdigest())
  self.compiler=self.manifest['tools']['nyrac']['sha256'];self.expected=dict(regression=537,compiler_units={'kernel':85,'stdlib':362,'yaml':84,'json':34,'package_core':49,'frontend':470,'nyra':96,'nllvm':11,'ntest':5},tool_units={'nyra-analyzer':30,'nyra':16})
  self.data={'share/nyra/build-evidence.json':dict(source_revision='a'*40,validation_policy='g1-g2-v1',source_worktree_clean=True,product_source_matches_generations=True,full_regression=True,full_unit=True,seed_compiler_sha256=self.seed,fixed_point=dict(identical=True,G1_sha256=self.compiler,G2_sha256=self.compiler)),
   'share/nyra/validation/compiler-regression.json':dict(compiler_sha256=self.compiler,total=537,passed=537,failed=0,suite_inventory_sha256='e'*64),
   'share/nyra/validation/units.json':self.units(self.expected['compiler_units'],self.compiler)}
  for name,package in [('nyra','package_manager'),('nyra-analyzer','analyzer')]:self.data['share/nyra/validation/tools/'+name+'.json']=dict(status='complete',builder_sha256=self.seed,product_sha256=self.manifest['tools'][name]['sha256'],test_source_sha256='d'*64,units=self.units({package:self.expected['tool_units'][name]},self.seed))
 def units(self,counts,compiler):return dict(status='complete',compiler_sha256=compiler,passed=sum(counts.values()),suites=[dict(package=k,summary=dict(status='ok',passed=v,ran=v,failed=0,skipped=0,exit_code=0,ignored=0)) for k,v in counts.items()])
 def freeze(self):
  for name,value in self.data.items():
   file=self.root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(json.dumps(value))
  self.manifest['files']=[dict(path=p.relative_to(self.root).as_posix(),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in self.root.rglob('*') if p.is_file() and p.name!='manifest.json'];(self.root/'manifest.json').write_text(json.dumps(self.manifest))
 def verify(self):return verify_complete(self.root,self.manifest,self.plan,'linux-x86_64',self.expected)
 def test_complete_gates_retained_without_execution(self):
  self.freeze();r=self.verify();self.assertEqual(r['compiler_units'],1196);self.assertFalse(r['compiler_rebuilt']);self.assertFalse(r['complete_suites_repeated'])
 def test_partial_regression_rejected(self):
  self.data['share/nyra/validation/compiler-regression.json']['passed']=536;self.freeze()
  with self.assertRaisesRegex(ValueError,'regression'):self.verify()
 def test_missing_compiler_suite_rejected(self):
  self.data['share/nyra/validation/units.json']['suites'].pop();self.freeze()
  with self.assertRaisesRegex(ValueError,'unit inventory'):self.verify()
 def test_failed_tool_suite_rejected(self):
  self.data['share/nyra/validation/tools/nyra.json']['units']['suites'][0]['summary']['failed']=1;self.freeze()
  with self.assertRaisesRegex(ValueError,'retained unit suite'):self.verify()
 def test_substituted_product_rejected(self):
  self.freeze();(self.root/'bin/nyra').write_bytes(b'substituted')
  with self.assertRaisesRegex(ValueError,'file identity'):self.verify()
 def test_different_tool_builder_rejected(self):
  self.data['share/nyra/validation/tools/nyra-analyzer.json']['builder_sha256']='c'*64;self.freeze()
  with self.assertRaisesRegex(ValueError,'product/builder'):self.verify()
 def test_different_fixed_point_rejected(self):
  self.data['share/nyra/build-evidence.json']['fixed_point']['G1_sha256']='c'*64;self.freeze()
  with self.assertRaisesRegex(ValueError,'fixed point'):self.verify()
 def test_extra_payload_rejected(self):
  self.freeze();(self.root/'unexpected').write_bytes(b'unlisted')
  with self.assertRaisesRegex(ValueError,'inventory is incomplete'):self.verify()
 def test_missing_gate_receipt_rejected(self):
  del self.data['share/nyra/validation/tools/nyra.json'];self.freeze()
  with self.assertRaisesRegex(ValueError,'missing retained gate'):self.verify()
 def test_frozen_source_not_relabelled(self):
  self.data['share/nyra/build-evidence.json']['source_revision']='b'*40;self.freeze()
  with self.assertRaisesRegex(ValueError,'frozen inputs'):self.verify()

if __name__=='__main__':unittest.main()

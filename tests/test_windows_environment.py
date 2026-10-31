"""Windows resource builds require the installed SDK and MSVC environment."""
import os
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from build_sdk import native_environment

class WindowsResources(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.programs=self.root/'Program Files (x86)';self.cache=self.root/'cache'
  self.addCleanup(patch.stopall);patch.dict(os.environ,{'ProgramFiles(x86)':str(self.programs),'RUNNER_TOOL_CACHE':str(self.cache),'PATH':'prior-path'},clear=True).start()
  header=self.cache/'nyra-providers/openssl-3.5.9/include/openssl/ssl.h';header.parent.mkdir(parents=True);header.write_text('provider')
  visual=self.programs/'Microsoft Visual Studio/2022/BuildTools';version=visual/'VC/Auxiliary/Build/Microsoft.VCToolsVersion.default.txt';version.parent.mkdir(parents=True);version.write_text('14.44.35207\n');self.vc=visual/'VC/Tools/MSVC/14.44.35207';(self.vc/'include').mkdir(parents=True);(self.vc/'include/vcruntime.h').write_text('runtime')
 def sdk(self,version):
  base=self.programs/'Windows Kits/10';rc=base/'bin'/version/'x64/rc.exe';rc.parent.mkdir(parents=True);rc.write_bytes(b'installed rc');includes=base/'Include'/version
  for folder,name in [('um','winver.h'),('shared','winapifamily.h'),('ucrt','corecrt.h')]:
   (includes/folder).mkdir(parents=True);(includes/folder/name).write_text('sdk')
  return rc,includes
 def test_numeric_sdk_order_and_complete_native_header_environment(self):
  self.sdk('10.0.9.0');rc,includes=self.sdk('10.0.10.0');env=native_environment(self.root/'sdk','windows-x86_64')
  self.assertEqual(env['RC'],str(rc));self.assertEqual(env['PATH'].split(os.pathsep)[0],str(rc.parent));self.assertIn(str(self.vc/'include'),env['INCLUDE'].split(os.pathsep));self.assertIn(str(includes/'um'),env['INCLUDE'].split(os.pathsep));self.assertIn(str(includes/'shared'),env['INCLUDE'].split(os.pathsep));self.assertIn(str(self.vc/'lib/x64'),env['LIB'].split(os.pathsep))
 def test_missing_rc_fails_before_qualification(self):
  with self.assertRaisesRegex(ValueError,'resource compiler'):native_environment(self.root/'sdk','windows-x86_64')
 def test_missing_paired_resource_headers_fails_before_qualification(self):
  _,includes=self.sdk('10.0.10.0');(includes/'um/winver.h').unlink()
  with self.assertRaisesRegex(ValueError,'resource headers'):native_environment(self.root/'sdk','windows-x86_64')
 def test_missing_msvc_runtime_headers_fails_before_qualification(self):
  self.sdk('10.0.10.0');(self.vc/'include/vcruntime.h').unlink()
  with self.assertRaisesRegex(ValueError,'MSVC development headers'):native_environment(self.root/'sdk','windows-x86_64')

if __name__=='__main__':unittest.main()

"""Foreign target flags and ELF archive order are platform contracts."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from runtime_consumer import split_link_inputs, archive_link_flags

class RuntimeLinkTests(unittest.TestCase):
    def test_objects_precede_grouped_elf_dependency_archives(self):
        objects, archives = split_link_inputs(['kernel.a','stdlib.a','main.o','ntest.a'])
        self.assertEqual(objects, ['main.o'])
        self.assertEqual(archive_link_flags('linux-x86_64', archives, Path('platform.a')),
                         ['-Wl,--start-group','kernel.a','stdlib.a','ntest.a','platform.a','-Wl,--end-group'])
    def test_coff_does_not_receive_elf_linker_options(self):
        objects, archives = split_link_inputs(['kernel.lib','main.obj','ntest.a'])
        self.assertEqual(objects, ['main.obj'])
        self.assertEqual(archive_link_flags('windows-arm64',archives,Path('platform.a')),
                         ['kernel.lib','ntest.a','platform.a'])

if __name__=='__main__':unittest.main()

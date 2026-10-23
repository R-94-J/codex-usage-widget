import os
import tempfile
import unittest
try:
    from codex_meter.instance import InstanceGuard
except ImportError:
    InstanceGuard=None

@unittest.skipUnless(os.name=='nt','Windows instance primitives')
class InstanceTests(unittest.TestCase):
    def test_duplicate_launch_signals_existing_instance(self):
        self.assertIsNotNone(InstanceGuard,'instance guard missing')
        with tempfile.TemporaryDirectory() as directory:
            first=InstanceGuard(directory);self.addCleanup(first.close)
            second=InstanceGuard(directory);self.addCleanup(second.close)
            self.assertTrue(first.primary);self.assertFalse(second.primary)
            second.request_show()
            self.assertTrue(first.consume_show_request())
            self.assertFalse(first.consume_show_request())

    def test_closed_instance_can_restart(self):
        self.assertIsNotNone(InstanceGuard,'instance guard missing')
        with tempfile.TemporaryDirectory() as directory:
            first=InstanceGuard(directory);first.close()
            second=InstanceGuard(directory);self.addCleanup(second.close)
            self.assertTrue(second.primary)

if __name__=='__main__':unittest.main()

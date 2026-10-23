import os
import unittest
try:
    from codex_meter.tray import TrayIcon
except ImportError:
    TrayIcon=None

@unittest.skipUnless(os.name=='nt','Windows tray')
class TrayTests(unittest.TestCase):
    def test_tray_window_has_clean_lifecycle(self):
        self.assertIsNotNone(TrayIcon,'tray lifecycle is missing')
        tray=TrayIcon(lambda action:None)
        self.assertTrue(tray.ready.wait(5),'tray thread failed to initialize')
        self.assertGreater(tray.hwnd,0)
        tray.close()
        self.assertFalse(tray.thread.is_alive())

if __name__=='__main__':unittest.main()

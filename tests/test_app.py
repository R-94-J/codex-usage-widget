import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from tests.test_usage import meta,context,event,counts

ROOT=Path(__file__).resolve().parents[1]

class AppTests(unittest.TestCase):
    def test_diagnostic_snapshot_uses_real_collector_without_ui(self):
        main=ROOT/'main.py'
        self.assertTrue(main.exists(),'application entry point is missing')
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);home=base/'codex';logs=home/'sessions';logs.mkdir(parents=True)
            rows=[meta(),context(),event(counts(1000,100,200,300,50),counts(1000,100,200,300,50))]
            (logs/'a.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
            output=base/'snapshot.json'
            result=subprocess.run([sys.executable,str(main),'--snapshot','--codex-home',str(home),
                                   '--state-dir',str(base/'state'),'--output',str(output),
                                   '--now','2026-10-23T14:00:00+08:00'],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(data['today_tokens'],1100)
            self.assertEqual(data['session_tokens'],1100)
            self.assertEqual(data['session_cost'],'$0.0140')
            self.assertFalse((base/'state/settings.json').exists())
            self.assertNotIn('payload',data)

    def test_background_override_requires_explicit_test_target(self):
        main=ROOT/'main.py';self.assertTrue(main.exists(),'application entry point is missing')
        result=subprocess.run([sys.executable,str(main),'--allow-background'],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0)

if __name__=='__main__':unittest.main()

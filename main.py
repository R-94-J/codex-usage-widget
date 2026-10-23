"""Codex Usage Widget: offline diagnostics or a Windows attached usage bar."""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description='Codex 用量条：本机 token 统计和 API 参考估算')
    parser.add_argument('--snapshot',action='store_true',help='导出仅含用量的 JSON，不启动界面')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--codex-home',type=Path,default=Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex'))
    parser.add_argument('--state-dir',type=Path)
    parser.add_argument('--now',help='仅用于 snapshot 测试的 ISO8601 时间')
    parser.add_argument('--target-hwnd',type=int,help=argparse.SUPPRESS)
    parser.add_argument('--allow-background',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--diagnostics',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--exit-after',type=float,default=0,help=argparse.SUPPRESS)
    parser.add_argument('--expanded',action='store_true',help=argparse.SUPPRESS)
    options=parser.parse_args()
    if options.allow_background and not options.target_hwnd:
        parser.error('--allow-background requires an explicit --target-hwnd test window')
    if options.now and not options.snapshot:parser.error('--now is only for --snapshot')
    from codex_meter.settings import default_state_dir,load_settings
    state=options.state_dir or default_state_dir()
    settings,warning=load_settings(state/'settings.json')
    if options.snapshot:
        from codex_meter.usage import UsageCollector
        from codex_meter.pricing import PriceBook
        from codex_meter.diagnostics import snapshot_payload
        now=datetime.fromisoformat(options.now.replace('Z','+00:00')) if options.now else None
        snapshot=UsageCollector(options.codex_home,settings['utc_offset_minutes']).poll(now)
        payload=snapshot_payload(snapshot,PriceBook(settings),settings['pinned_session_id'])
        if warning:payload['settings_warning']=warning
        text=json.dumps(payload,ensure_ascii=False,indent=2)+'\n'
        if options.output:
            options.output.parent.mkdir(parents=True,exist_ok=True)
            options.output.write_text(text,encoding='utf-8')
        elif sys.stdout:print(text,end='')
        else:
            state.mkdir(parents=True,exist_ok=True)
            (state/'snapshot.json').write_text(text,encoding='utf-8')
        return 0
    if os.name!='nt':parser.error('Windows 10/11 is required for the attached widget')
    from codex_meter.instance import InstanceGuard
    guard=InstanceGuard(state)
    if not guard.primary:
        guard.request_show();guard.close();return 0
    try:
        from codex_meter.app import UsageApp
        app=UsageApp(options,state,settings,warning,guard)
        app.run()
    finally:guard.close()
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        from codex_meter.settings import default_state_dir
        state=default_state_dir();state.mkdir(parents=True,exist_ok=True)
        error=state/'last-error.log';error.write_text(traceback.format_exc(),encoding='utf-8')
        if sys.stderr:traceback.print_exc()
        if os.name=='nt' and getattr(sys,'frozen',False):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,f'用量条启动失败。诊断文件：\n{error}', 'Codex 用量条',0x10)
        raise SystemExit(1)

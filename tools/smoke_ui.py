"""Exercise only an explicitly-created test host, never the real Codex window."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def capture_window(hwnd,path):
    from PIL import Image
    u=C.WinDLL('user32',use_last_error=True);g=C.WinDLL('gdi32',use_last_error=True)
    u.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)];u.GetWindowRect.restype=W.BOOL
    u.GetWindowDC.argtypes=[W.HWND];u.GetWindowDC.restype=W.HDC
    u.ReleaseDC.argtypes=[W.HWND,W.HDC];u.ReleaseDC.restype=C.c_int
    u.PrintWindow.argtypes=[W.HWND,W.HDC,W.UINT];u.PrintWindow.restype=W.BOOL
    for n,a,r in [('CreateCompatibleDC',[W.HDC],W.HDC),('CreateCompatibleBitmap',[W.HDC,C.c_int,C.c_int],W.HBITMAP),
                  ('SelectObject',[W.HDC,W.HGDIOBJ],W.HGDIOBJ),('DeleteObject',[W.HGDIOBJ],W.BOOL),
                  ('DeleteDC',[W.HDC],W.BOOL),('GetDIBits',[W.HDC,W.HBITMAP,W.UINT,W.UINT,W.LPVOID,W.LPVOID,W.UINT],C.c_int)]:
        f=getattr(g,n);f.argtypes=a;f.restype=r
    class HEADER(C.Structure):
        _fields_=[('size',W.DWORD),('width',W.LONG),('height',W.LONG),('planes',W.WORD),('bits',W.WORD),
                  ('compression',W.DWORD),('image_size',W.DWORD),('xppm',W.LONG),('yppm',W.LONG),
                  ('colors',W.DWORD),('important',W.DWORD)]
    rect=W.RECT();assert u.GetWindowRect(hwnd,C.byref(rect))
    width,height=rect.right-rect.left,rect.bottom-rect.top
    dc=u.GetWindowDC(hwnd);mem=g.CreateCompatibleDC(dc);bitmap=g.CreateCompatibleBitmap(dc,width,height)
    old=g.SelectObject(mem,bitmap)
    try:
        assert u.PrintWindow(hwnd,mem,2),'PrintWindow failed'
        header=HEADER(C.sizeof(HEADER),width,-height,1,32,0,0,0,0,0,0)
        buf=C.create_string_buffer(width*height*4)
        assert g.GetDIBits(mem,bitmap,0,height,buf,C.byref(header),0)==height,'GetDIBits failed'
        image=Image.frombuffer('RGB',(width,height),buf.raw,'raw','BGRX',0,1)
        image.save(path)
        assert len(image.getcolors(width*height) or [])>10,'empty screenshot'
        return [width,height]
    finally:
        g.SelectObject(mem,old);g.DeleteObject(bitmap);g.DeleteDC(mem);u.ReleaseDC(hwnd,dc)


def run(executable=None):
    from codex_meter.native import configure_dpi_awareness
    configure_dpi_awareness()
    u=C.WinDLL('user32',use_last_error=True)
    prototypes={
        'CreateWindowExW':([W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID],W.HWND),
        'ShowWindow':([W.HWND,C.c_int],W.BOOL),'DestroyWindow':([W.HWND],W.BOOL),
        'GetWindowThreadProcessId':([W.HWND,C.POINTER(W.DWORD)],W.DWORD),
        'GetCursorPos':([C.POINTER(W.POINT)],W.BOOL),
        'SetCursorPos':([C.c_int,C.c_int],W.BOOL),
        'WindowFromPoint':([W.POINT],W.HWND),
        'GetAncestor':([W.HWND,W.UINT],W.HWND),
        'GetForegroundWindow':([],W.HWND),'SetWindowPos':([W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT],W.BOOL),
        'SendMessageW':([W.HWND,W.UINT,W.WPARAM,W.LPARAM],C.c_ssize_t),
        'PostMessageW':([W.HWND,W.UINT,W.WPARAM,W.LPARAM],W.BOOL),
        'PeekMessageW':([C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT],W.BOOL),
        'TranslateMessage':([C.POINTER(W.MSG)],W.BOOL),'DispatchMessageW':([C.POINTER(W.MSG)],C.c_ssize_t),
    }
    for n,(a,r) in prototypes.items():f=getattr(u,n);f.argtypes=a;f.restype=r
    host=u.CreateWindowExW(0x08000080,'STATIC','Codex Usage Widget - automated test host',0x00CF0000,
                            80,100,1060,500,None,None,None,None)
    assert host,'test host creation failed'
    u.ShowWindow(host,4)
    foreground=int(u.GetForegroundWindow() or 0)
    process=None
    artifacts=ROOT/'docs/evidence';artifacts.mkdir(exist_ok=True)
    result={}
    temp_context=None
    def pump():
        message=W.MSG()
        while u.PeekMessageW(C.byref(message),None,0,0,1):
            u.TranslateMessage(C.byref(message));u.DispatchMessageW(C.byref(message))
    try:
        temp_context=tempfile.TemporaryDirectory(prefix='codex-meter-ui-',ignore_cleanup_errors=True)
        with temp_context as directory:
            base=Path(directory)
            assert base.resolve().parent==Path(tempfile.gettempdir()).resolve() and base.name.startswith('codex-meter-ui-')
            home=base/'codex';logs=home/'sessions';logs.mkdir(parents=True)
            from tests.test_usage import meta,context,event,counts
            from datetime import datetime,timezone
            now=datetime.now(timezone.utc).isoformat()
            rows=[meta(ts=now),context(ts=now),event(counts(1000,100,200,300,50),counts(1000,100,200,300,50),ts=now)]
            logfile=logs/'fixture.jsonl';logfile.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
            diagnostics=base/'window.json';err=base/'stderr.log'
            command=([str(executable)] if executable else [sys.executable,str(ROOT/'main.py')])
            command+=['--target-hwnd',str(int(host)),'--allow-background','--codex-home',str(home),
                      '--state-dir',str(base/'state'),'--diagnostics',str(diagnostics)]
            with err.open('w',encoding='utf-8') as errorfile:
                process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=errorfile,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                def wait_for(predicate,description,timeout=12):
                    until=time.monotonic()+timeout;last={}
                    while time.monotonic()<until:
                        pump()
                        if process.poll() is not None:
                            raise AssertionError(f'GUI exited ({process.returncode}): '+err.read_text(encoding='utf-8'))
                        try:last=json.loads(diagnostics.read_text(encoding='utf-8'))
                        except (OSError,ValueError):pass
                        if predicate(last):return last
                        time.sleep(.06)
                    raise AssertionError(description+': '+json.dumps(last,ensure_ascii=False))
                def click_button(data,name):
                    class MOUSE(C.Structure):
                        _fields_=[('dx',W.LONG),('dy',W.LONG),('data',W.DWORD),('flags',W.DWORD),('time',W.DWORD),('extra',C.c_size_t)]
                    class UNION(C.Union):_fields_=[('mouse',MOUSE)]
                    class INPUT(C.Structure):_fields_=[('type',W.DWORD),('union',UNION)]
                    u.SendInput.argtypes=[W.UINT,C.POINTER(INPUT),C.c_int];u.SendInput.restype=W.UINT
                    old=W.POINT();assert u.GetCursorPos(C.byref(old))
                    x,y=data['buttons'][name];gx,gy=data['geometry'][:2]
                    target=W.POINT(gx+x,gy+y)
                    assert int(u.GetAncestor(u.WindowFromPoint(target),2) or 0)==data['hwnd'],'refusing to click outside test widget'
                    try:
                        assert u.SetCursorPos(target.x,target.y)
                        down=INPUT(0,UNION(mouse=MOUSE(0,0,0,2,0,0)))
                        up=INPUT(0,UNION(mouse=MOUSE(0,0,0,4,0,0)))
                        assert u.SendInput(1,C.byref(down),C.sizeof(INPUT))==1
                        time.sleep(.06)
                        assert u.SendInput(1,C.byref(up),C.sizeof(INPUT))==1
                    finally:u.SetCursorPos(old.x,old.y)
                d=wait_for(lambda d:d.get('visible') and d.get('session_tokens')==1100,'initial data/show')
                after=int(u.GetForegroundWindow() or 0)
                after_pid=W.DWORD();u.GetWindowThreadProcessId(after,C.byref(after_pid))
                focus_evidence={'foreground_before':foreground,'foreground_after':after,'after_pid':after_pid.value,
                                'widget_pid':d['pid'],'widget_hwnd':d['hwnd']}
                (artifacts/'focus-startup.json').write_text(json.dumps(focus_evidence),encoding='utf-8')
                assert after==foreground,'startup foreground changed: '+json.dumps(focus_evidence)
                result['initial_no_focus_steal']=True
                hwnd=d['hwnd'];initial=d['geometry'];canvas=d['canvas_hwnd']
                capture_window(hwnd,artifacts/'widget-test-compact.png')
                u.SetWindowPos(host,None,130,180,1060,500,0x10|0x4)
                moved=wait_for(lambda d:d.get('geometry',[0,0])[:2]==[initial[0]+50,initial[1]+80],'move follow')
                result['follow_move']=True
                u.ShowWindow(host,6)
                wait_for(lambda d:d.get('visible') is False,'minimize hide')
                result['minimize_hide']=True
                u.ShowWindow(host,4)
                wait_for(lambda d:d.get('visible') is True,'restore show')
                result['restore_show']=True
                stamp=datetime.now(timezone.utc).isoformat()
                with logfile.open('a',encoding='utf-8') as f:
                    f.write(json.dumps(event(counts(2000,200,400,600,100),counts(1000,100,200,300,50),ts=stamp))+'\n')
                d=wait_for(lambda d:d.get('session_tokens')==2200,'incremental refresh')
                result['live_refresh']=True
                click_button(d,'expand')
                d=wait_for(lambda d:d.get('expanded') and d.get('geometry',[0,0,0,0])[3]>100,'expand action')
                result['expand_button']=True
                result['expanded_image_size']=capture_window(hwnd,artifacts/'widget-test-expanded.png')
                duplicate=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                assert duplicate.returncode==0,'duplicate launch failed'
                assert process.poll() is None,'duplicate launch replaced live process'
                result['single_instance']=True
                assert int(u.GetForegroundWindow() or 0)==foreground,'refresh or expand stole focus'
                result['refresh_no_focus_steal']=True
                d=wait_for(lambda d:d.get('hwnd')==hwnd,'stable instance')
                result['tray_available']=d.get('tray_available')
                click_button(d,'settings')
                settings_state=wait_for(lambda d:d.get('settings_hwnd',0)>0,'settings dialog open')
                result['settings_image_size']=capture_window(settings_state['settings_hwnd'],artifacts/'widget-test-settings.png')
                assert u.PostMessageW(settings_state['settings_hwnd'],0x10,0,0)
                d=wait_for(lambda d:d.get('settings_hwnd',0)==0,'settings dialog close')
                result['settings_dialog']=True
                click_button(d,'exit')
                process.wait(timeout=8)
                assert process.returncode==0,err.read_text(encoding='utf-8')
                result['graceful_exit']=True
        return result
    finally:
        if process and process.poll() is None:process.terminate();process.wait(timeout=5)
        u.DestroyWindow(host)
        if temp_context:temp_context.cleanup()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--exe',type=Path);args=parser.parse_args()
    result=run(args.exe)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    name='ui-smoke-packaged.json' if args.exe else 'ui-smoke-source.json'
    (ROOT/'docs/evidence'/name).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

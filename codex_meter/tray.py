"""Small Win32 tray icon on its own message-loop thread; no third-party runtime."""
import ctypes
import os
import threading
from ctypes import wintypes as W


class TrayIcon:
    def __init__(self,callback,icon_path=None):
        self.callback=callback;self.icon_path=icon_path
        self.ready=threading.Event();self.hwnd=0;self.available=False;self.error=''
        self.thread=threading.Thread(target=self._run,name='usage-tray',daemon=True)
        self.thread.start()

    def _run(self):
        if os.name!='nt':
            self.error='Windows is required';self.ready.set();return
        user=ctypes.WinDLL('user32',use_last_error=True)
        shell=ctypes.WinDLL('shell32',use_last_error=True)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        LRESULT=ctypes.c_ssize_t
        PROC=ctypes.WINFUNCTYPE(LRESULT,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        class GUID(ctypes.Structure):
            _fields_=[('Data1',W.DWORD),('Data2',W.WORD),('Data3',W.WORD),('Data4',W.BYTE*8)]
        class NID(ctypes.Structure):
            _fields_=[('cbSize',W.DWORD),('hWnd',W.HWND),('uID',W.UINT),('uFlags',W.UINT),
                      ('uCallbackMessage',W.UINT),('hIcon',W.HICON),('szTip',W.WCHAR*128),
                      ('dwState',W.DWORD),('dwStateMask',W.DWORD),('szInfo',W.WCHAR*256),
                      ('uVersion',W.UINT),('szInfoTitle',W.WCHAR*64),('dwInfoFlags',W.DWORD),
                      ('guidItem',GUID),('hBalloonIcon',W.HICON)]
        class WC(ctypes.Structure):
            _fields_=[('style',W.UINT),('lpfnWndProc',PROC),('cbClsExtra',ctypes.c_int),
                      ('cbWndExtra',ctypes.c_int),('hInstance',W.HINSTANCE),('hIcon',W.HICON),
                      ('hCursor',W.HANDLE),('hbrBackground',W.HBRUSH),
                      ('lpszMenuName',W.LPCWSTR),('lpszClassName',W.LPCWSTR)]
        specs={
            'RegisterClassW':([ctypes.POINTER(WC)],W.WORD),
            'UnregisterClassW':([W.LPCWSTR,W.HINSTANCE],W.BOOL),
            'CreateWindowExW':([W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,ctypes.c_int,ctypes.c_int,
                               ctypes.c_int,ctypes.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID],W.HWND),
            'DefWindowProcW':([W.HWND,W.UINT,W.WPARAM,W.LPARAM],LRESULT),
            'DestroyWindow':([W.HWND],W.BOOL),'PostQuitMessage':([ctypes.c_int],None),
            'GetMessageW':([ctypes.POINTER(W.MSG),W.HWND,W.UINT,W.UINT],ctypes.c_int),
            'TranslateMessage':([ctypes.POINTER(W.MSG)],W.BOOL),
            'DispatchMessageW':([ctypes.POINTER(W.MSG)],LRESULT),
            'RegisterWindowMessageW':([W.LPCWSTR],W.UINT),
            'LoadIconW':([W.HINSTANCE,W.LPCWSTR],W.HICON),
            'LoadImageW':([W.HINSTANCE,W.LPCWSTR,W.UINT,ctypes.c_int,ctypes.c_int,W.UINT],W.HANDLE),
            'DestroyIcon':([W.HICON],W.BOOL),
        }
        for name,(args,result) in specs.items():
            fn=getattr(user,name);fn.argtypes=args;fn.restype=result
        shell.Shell_NotifyIconW.argtypes=[W.DWORD,ctypes.POINTER(NID)];shell.Shell_NotifyIconW.restype=W.BOOL
        kernel.GetModuleHandleW.argtypes=[W.LPCWSTR];kernel.GetModuleHandleW.restype=W.HMODULE
        nid=NID();instance=kernel.GetModuleHandleW(None)
        taskbar=user.RegisterWindowMessageW('TaskbarCreated');owned_icon=False;icon=None
        class_name=f'CodexUsageWidget.Tray.{os.getpid()}.{id(self)}'
        def notify():
            self.available=bool(shell.Shell_NotifyIconW(0,ctypes.byref(nid)))
        @PROC
        def procedure(hwnd,msg,wparam,lparam):
            if msg==0x8001:
                if lparam in (0x203,0x205):
                    try:self.callback('settings' if lparam==0x203 else 'menu')
                    except Exception:pass
                return 0
            if msg==taskbar and nid.hWnd:
                notify();return 0
            if msg==0x10:
                user.DestroyWindow(hwnd);return 0
            if msg==0x2:
                user.PostQuitMessage(0);return 0
            return user.DefWindowProcW(hwnd,msg,wparam,lparam)
        try:
            wc=WC();wc.lpfnWndProc=procedure;wc.hInstance=instance;wc.lpszClassName=class_name
            if not user.RegisterClassW(ctypes.byref(wc)):raise ctypes.WinError(ctypes.get_last_error())
            hwnd=user.CreateWindowExW(0,class_name,'Codex Usage Widget tray',0,0,0,0,0,None,None,instance,None)
            if not hwnd:raise ctypes.WinError(ctypes.get_last_error())
            self.hwnd=int(hwnd)
            if self.icon_path:
                icon=user.LoadImageW(None,str(self.icon_path),1,0,0,0x10|0x40)
                owned_icon=bool(icon)
            if not icon:
                icon=user.LoadIconW(None,ctypes.cast(ctypes.c_void_p(32512),W.LPCWSTR))
            nid.cbSize=ctypes.sizeof(nid);nid.hWnd=hwnd;nid.uID=1;nid.uFlags=1|2|4
            nid.uCallbackMessage=0x8001;nid.hIcon=icon
            nid.szTip='Codex 用量条 · API参考估算\n双击设置，右键菜单；不会读取 API 密钥'
            notify();self.ready.set()
            msg=W.MSG()
            while user.GetMessageW(ctypes.byref(msg),None,0,0)>0:
                user.TranslateMessage(ctypes.byref(msg));user.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self.error=f'{type(exc).__name__}: {exc}'
        finally:
            if nid.hWnd:shell.Shell_NotifyIconW(2,ctypes.byref(nid))
            if owned_icon and icon:user.DestroyIcon(icon)
            user.UnregisterClassW(class_name,instance)
            self.hwnd=0;self.available=False;self.ready.set()

    def close(self):
        self.ready.wait(2)
        if self.hwnd:
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];user.PostMessageW.restype=W.BOOL
            user.PostMessageW(self.hwnd,0x10,0,0)
        self.thread.join(timeout=3)

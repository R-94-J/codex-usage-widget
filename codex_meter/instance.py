"""Per-user, per-state-directory single instance with a reopen event."""
import ctypes
import hashlib
import os
from ctypes import wintypes
from pathlib import Path

class InstanceGuard:
    def __init__(self,directory):
        if os.name!='nt':raise OSError('Windows is required')
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        for name,args,restype in (
            ('CreateMutexW',[wintypes.LPVOID,wintypes.BOOL,wintypes.LPCWSTR],wintypes.HANDLE),
            ('CreateEventW',[wintypes.LPVOID,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR],wintypes.HANDLE),
            ('SetEvent',[wintypes.HANDLE],wintypes.BOOL),
            ('WaitForSingleObject',[wintypes.HANDLE,wintypes.DWORD],wintypes.DWORD),
            ('CloseHandle',[wintypes.HANDLE],wintypes.BOOL)):
            function=getattr(self.kernel,name);function.argtypes=args;function.restype=restype
        identity=str(Path(directory).resolve()).casefold()
        digest=hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
        prefix='Local\\CodexUsageWidget.'+digest
        ctypes.set_last_error(0)
        self.mutex=self.kernel.CreateMutexW(None,False,prefix+'.mutex')
        error=ctypes.get_last_error()
        if not self.mutex:raise ctypes.WinError(error)
        self.primary=error!=183
        self.event=self.kernel.CreateEventW(None,False,False,prefix+'.show')
        if not self.event:
            self.kernel.CloseHandle(self.mutex);self.mutex=None
            raise ctypes.WinError(ctypes.get_last_error())

    def request_show(self):
        if self.event:self.kernel.SetEvent(self.event)

    def consume_show_request(self):
        return bool(self.event and self.kernel.WaitForSingleObject(self.event,0)==0)

    def close(self):
        for name in ('event','mutex'):
            handle=getattr(self,name,None)
            if handle:
                self.kernel.CloseHandle(handle);setattr(self,name,None)

"""Non-activating Tk usage strip attached to the foreground Codex desktop host."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

from .diagnostics import snapshot_payload
from .native import NativeWindows,WindowInfo,anchor_position,should_show,configure_dpi_awareness
from .presentation import select_session,summarize_cost,format_tokens
from .pricing import PriceBook
from .settings import save_settings
from .tray import TrayIcon
from .usage import UsageCollector,sum_usage

BG='#111820';BORDER='#2a3543';TEXT='#e7eef5';MUTED='#8c9aab';MINT='#68e2bb';GOLD='#f5c476'
WIDTH=716;SMALL_HEIGHT=66;DETAIL_HEIGHT=250


def resources():
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))


class UsageApp:
    def __init__(self,options,state,settings,warning,guard):
        configure_dpi_awareness()
        self.options=options;self.state=state;self.settings=settings;self.guard=guard
        self.warning=warning;self.book=PriceBook(settings);self.snapshot=None;self.host=None
        self.visible=False;self.closed=False;self.dialog=None;self.menu_open=False
        self.messages=queue.Queue(maxsize=1);self.actions=queue.Queue();self.stop=threading.Event()
        self.state.mkdir(parents=True,exist_ok=True)
        logging.basicConfig(filename=str(self.state/'widget.log'),level=logging.WARNING,
                            format='%(asctime)s %(levelname)s %(message)s')
        self.root=tk.Tk();self.root.withdraw();self.root.title('Codex Usage Widget')
        self.root.overrideredirect(True);self.root.configure(bg=BG)
        try:self.root.iconbitmap(default=str(resources()/'assets/icon.ico'))
        except tk.TclError:pass
        self.root.protocol('WM_DELETE_WINDOW',self.quit)
        self.root.report_callback_exception=self._tk_error
        self.native=NativeWindows()
        self.root.update_idletasks()
        self.hwnd=self.native.root_window(self.root.winfo_id())
        self.native.configure_overlay(self.hwnd)
        self.u32=ctypes.WinDLL('user32',use_last_error=True)
        self.u32.IsWindowVisible.argtypes=[wintypes.HWND];self.u32.IsWindowVisible.restype=wintypes.BOOL
        try:
            self.u32.GetDpiForWindow.argtypes=[wintypes.HWND];self.u32.GetDpiForWindow.restype=wintypes.UINT
            dpi=self.u32.GetDpiForWindow(self.hwnd) or 96
        except AttributeError:dpi=96
        self.scale=max(1.0,min(3.0,dpi/96))
        self.fonts={}
        self.expanded=bool(settings['expanded'] or options.expanded)
        self.canvas=tk.Canvas(self.root,highlightthickness=0,bg=BG,borderwidth=0)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<ButtonPress-1>',self._press)
        self.canvas.bind('<ButtonRelease-1>',self._release)
        self.canvas.bind('<B1-Motion>',self._drag)
        self.canvas.bind('<Motion>',self._motion)
        self.canvas.bind('<Button-3>',self._context_menu)
        self.drag_start=None;self.pressed_button=None;self.input_trace=[];self.last_scan=0;self.last_diag=0;self.position=None
        self._resize();self._draw()
        # Map Tk's children once after NOACTIVATE is installed. All later visibility
        # changes go through Win32 SWP_NOACTIVATE rather than repeated deiconify().
        self.root.deiconify();self.root.update_idletasks()
        self.hwnd=self.native.root_window(self.root.winfo_id())
        self.native.configure_overlay(self.hwnd);self.native.hide(self.hwnd)
        self.tray=TrayIcon(self.actions.put,resources()/'assets/icon.ico')
        self.worker=threading.Thread(target=self._collect,name='usage-reader',daemon=True)
        self.worker.start()
        self.root.after(1,self._tick)
        if options.exit_after>0:self.root.after(int(options.exit_after*1000),self.quit)

    def _tk_error(self,kind,error,trace):
        logging.error('UI callback failed',exc_info=(kind,error,trace))
        self.warning='界面操作失败，请查看本机 widget.log。'

    def _collect(self):
        collector=UsageCollector(self.options.codex_home,self.settings['utc_offset_minutes'])
        while not self.stop.is_set():
            try:message=(collector.poll(),'')
            except Exception as error:
                logging.exception('Usage metadata scan failed')
                message=(None,f'日志读取暂时失败：{type(error).__name__}')
            try:self.messages.put_nowait(message)
            except queue.Full:
                try:self.messages.get_nowait()
                except queue.Empty:pass
                try:self.messages.put_nowait(message)
                except queue.Full:pass
            self.stop.wait(self.settings['poll_seconds'])

    def _font(self,size,bold=False,numeric=False):
        key=(size,bold,numeric)
        if key not in self.fonts:
            self.fonts[key]=tkfont.Font(self.root,family='Consolas' if numeric else 'Microsoft YaHei UI',
                                      size=-round(size*self.scale),weight='bold' if bold else 'normal')
        return self.fonts[key]

    def _text(self,x,y,text,size=11,color=TEXT,bold=False,numeric=False,max_width=None,anchor='w'):
        font=self._font(size,bold,numeric)
        if max_width:
            while size>9 and font.measure(text)>max_width*self.scale:
                size-=1;font=self._font(size,bold,numeric)
            if font.measure(text)>max_width*self.scale:
                while text and font.measure(text+'…')>max_width*self.scale:text=text[:-1]
                text+='…'
        self.canvas.create_text(x*self.scale,y*self.scale,text=text,fill=color,font=font,anchor=anchor)

    def _line(self,x1,y1,x2,y2,color=BORDER,width=1):
        self.canvas.create_line(x1*self.scale,y1*self.scale,x2*self.scale,y2*self.scale,fill=color,width=width*self.scale)

    def _resize(self):
        self.width=round(WIDTH*self.scale)
        self.height=round((DETAIL_HEIGHT if self.expanded else SMALL_HEIGHT)*self.scale)
        self.root.geometry(f'{self.width}x{self.height}')
        self.canvas.configure(width=self.width,height=self.height)
        self.root.update_idletasks()
        self.position=None

    def _draw(self):
        c=self.canvas;c.delete('all');s=self.scale
        c.create_rectangle(0,0,self.width-1,self.height-1,outline=BORDER,fill=BG,width=1)
        color=GOLD if self.warning or (self.snapshot and self.snapshot.errors) else MINT
        c.create_oval(16*s,16*s,22*s,22*s,fill=color,outline='')
        self._text(30,19,'CODEX USAGE' if not self.options.target_hwnd else 'CODEX · 测试数据',10,color,bold=True,max_width=120)
        session=select_session(self.snapshot,self.settings['pinned_session_id']) if self.snapshot else None
        current=summarize_cost(session.events if session else (),self.book)
        today=summarize_cost(self.snapshot.today_events if self.snapshot else (),self.book)
        model=session.model if session else '等待用量记录'
        self._text(16,44,model,10,MUTED,max_width=124)
        for x in (151,310,457):self._line(x,14,x,52)
        self._text(167,19,'锁定会话' if self.settings['pinned_session_id'] else '最近活跃会话',10,MUTED)
        self._text(167,43,format_tokens(session.total_tokens) if session else '—',20,TEXT,True,True,max_width=119)
        self._text(326,19,current.label if session else 'API参考估算',10,MUTED,max_width=120)
        self._text(326,43,current.display if session else '—',18,MINT,True,True,max_width=118)
        self._text(473,19,'今日 · 本机日志',10,MUTED)
        today_text=f'{format_tokens(self.snapshot.today_tokens)} / {today.display}' if self.snapshot else '正在扫描…'
        self._text(473,43,today_text,14,TEXT,True,True,max_width=150)
        # Draw icons rather than rely on platform emoji fonts.
        for x in (647,673,699):
            c.create_rectangle((x-11)*s,20*s,(x+11)*s,46*s,outline='',fill=BG)
        if self.expanded:
            self._line(642,35,647,30,MUTED,1.5);self._line(647,30,652,35,MUTED,1.5)
        else:
            self._line(642,30,647,35,MUTED,1.5);self._line(647,35,652,30,MUTED,1.5)
        c.create_oval(667*s,27*s,679*s,39*s,outline=MUTED,width=1.5*s)
        c.create_oval(671*s,31*s,675*s,35*s,outline=MUTED,width=1*s)
        for xa,ya,xb,yb in ((673,24,673,27),(673,39,673,42),(664,33,667,33),(679,33,682,33)):
            self._line(xa,ya,xb,yb,MUTED,1.5)
        self._line(695,29,703,37,MUTED,1.3);self._line(703,29,695,37,MUTED,1.3)
        if not self.expanded:return
        self._line(16,66,WIDTH-16,66)
        totals=sum_usage(session.events if session else ())
        for x,label,key in ((18,'输入 · 含缓存','input_tokens'),(191,'缓存读取','cached_input_tokens'),
                            (365,'缓存写入','cache_write_input_tokens'),(539,'输出 · 含推理','output_tokens')):
            self._text(x,88,label,11,MUTED)
            self._text(x,112,f'{totals[key]:,}',18,TEXT,True,True,max_width=154)
        self._text(18,142,'Token = 输入 + 输出。缓存与推理已包含在内，不重复相加。',11,MUTED,max_width=675)
        mode='锁定' if self.settings['pinned_session_id'] else '自动跟随最近活跃主会话（不等于当前选中的聊天）'
        self._text(18,165,mode,11,TEXT,max_width=675)
        sid=session.id if session else (self.settings['pinned_session_id'] or '尚未发现会话')
        self._text(18,187,f'会话 {sid}',10,MUTED,numeric=True,max_width=675)
        note='金额为 token 价格估算，不是平台实际扣费；第三方渠道请在设置中填单价。'
        self._text(18,211,note,11,GOLD,max_width=675)
        state=self.warning or (self.snapshot.errors[0] if self.snapshot and self.snapshot.errors else '')
        if not state and (current.unpriced_events or today.unpriced_events):
            state=f'未定价记录：会话 {current.unpriced_events} / 今日 {today.unpriced_events}；≥ 表示仅已定价部分。'
        if not state:
            stamp=self.snapshot.updated_at.astimezone().strftime('%H:%M:%S') if self.snapshot else '--:--:--'
            state=f'每 {self.settings["poll_seconds"]:g} 秒检查 · 今日按 UTC+8 · 刷新 {stamp} · 仅本机已记录用量'
        self._text(18,234,state,10,MUTED,max_width=675)

    def _get_host(self):
        if self.options.target_hwnd:
            hwnd=self.options.target_hwnd;rect=self.native.rect(hwnd)
            if not rect or not self.native.is_window(hwnd):return None
            return WindowInfo(hwnd,0,'Explicit test host',rect,bool(self.u32.IsWindowVisible(hwnd)),self.native.is_minimized(hwnd))
        now=time.monotonic()
        if now-self.last_scan>1 or self.host is None:
            self.last_scan=now;hosts=self.native.list_hosts()
            foreground=self.native.root_window(self.native.foreground_window())
            selected=next((h for h in hosts if h.hwnd==foreground),None)
            if selected is None and self.host:
                selected=next((h for h in hosts if h.hwnd==self.host.hwnd),None)
            if selected is None and hosts:selected=max(hosts,key=lambda h:h.rect.width*h.rect.height)
            self.host=selected
        if self.host:
            rect=self.native.rect(self.host.hwnd)
            if not rect or not self.native.is_window(self.host.hwnd):return None
            return replace(self.host,rect=rect,visible=bool(self.u32.IsWindowVisible(self.host.hwnd)),
                           minimized=self.native.is_minimized(self.host.hwnd))
        return None

    def _follow(self):
        self.host=self._get_host()
        roots={self.hwnd}
        if self.dialog and self.dialog.winfo_exists():roots.add(self.native.root_window(self.dialog.winfo_id()))
        foreground=self.native.root_window(self.native.foreground_window())
        visible=should_show(self.host,foreground,roots)
        if self.options.allow_background and self.host:
            visible=self.host.visible and not self.host.minimized
        if self.menu_open and self.host and not self.host.minimized:visible=True
        if not visible:
            if self.visible:self.native.hide(self.hwnd)
            self.visible=False;self.position=None;return
        x,y=anchor_position(self.host.rect,(self.width,self.height),
                            (self.settings['right_inset'],self.settings['top_inset']),self.native.work_area(self.host.hwnd))
        position=(x,y,self.width,self.height)
        if not self.visible or self.position!=position:
            self.native.show_overlay(self.hwnd,*position)
            self.position=position
        self.visible=True

    def _tick(self):
        if self.closed:return
        try:
            try:
                snapshot,warning=self.messages.get_nowait()
                if snapshot:self.snapshot=snapshot
                if warning:self.warning=warning
                self._draw()
            except queue.Empty:pass
            while True:
                try:action=self.actions.get_nowait()
                except queue.Empty:break
                if action=='settings':self.open_settings()
                elif action=='menu':self._context_menu()
            if self.guard.consume_show_request():
                if self.native.list_hosts() or self.options.target_hwnd:
                    self.last_scan=0;self.position=None
                else:self.open_settings()
            self._follow()
            if self.options.diagnostics and time.monotonic()-self.last_diag>.25:
                self.last_diag=time.monotonic();self._write_diagnostics()
        except Exception:
            logging.exception('Window follow/refresh failed')
            self.warning='刷新遇到异常，仍在后台重试；详情见 widget.log。'
        if not self.closed:self.root.after(250,self._tick)

    def _write_diagnostics(self):
        data=snapshot_payload(self.snapshot,self.book,self.settings['pinned_session_id']) if self.snapshot else {}
        rect=self.native.rect(self.hwnd)
        data.update(hwnd=self.hwnd,canvas_hwnd=self.canvas.winfo_id(),pid=os.getpid(),visible=self.visible,
                    host_hwnd=self.host.hwnd if self.host else None,expanded=self.expanded,
                    geometry=[rect.left,rect.top,rect.width,rect.height] if rect else [],
                    foreground=self.native.foreground_window(),tray_available=self.tray.available,input_trace=self.input_trace[-8:],
                    settings_hwnd=self.native.root_window(self.dialog.winfo_id()) if self.dialog and self.dialog.winfo_exists() else 0,
                    buttons={name:[round(x*self.scale),round(33*self.scale)] for name,x in (('expand',647),('settings',673),('exit',699))})
        path=self.options.diagnostics;path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp')
        try:
            temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,path)
        except OSError:pass

    def _button(self,x,y):
        x/=self.scale;y/=self.scale
        if 18<=y<=48:
            for name,center in (('expand',647),('settings',673),('exit',699)):
                if abs(x-center)<=12:return name
        return None

    def _press(self,event):
        if self.options.diagnostics:self.input_trace.append(['press',event.x,event.y,self._button(event.x,event.y)])
        self.pressed_button=self._button(event.x,event.y)
        if not self.pressed_button and self.host:
            self.drag_start=(event.x_root,event.y_root,self.settings['right_inset'],self.settings['top_inset'])

    def _drag(self,event):
        if not self.drag_start:return
        x,y,right,top=self.drag_start
        self.settings['right_inset']=max(0,min(10000,right-(event.x_root-x)))
        self.settings['top_inset']=max(0,min(10000,top+(event.y_root-y)))
        self.position=None;self._follow()

    def _release(self,event):
        if self.options.diagnostics:self.input_trace.append(['release',event.x,event.y,self._button(event.x,event.y),self.pressed_button])
        if self.drag_start:
            self.drag_start=None;self.persist()
        action=self._button(event.x,event.y)
        if action and action==self.pressed_button:
            if action=='expand':self.toggle_details()
            elif action=='settings':self.open_settings()
            elif action=='exit':self.quit()
        self.pressed_button=None

    def _motion(self,event):
        self.canvas.configure(cursor='hand2' if self._button(event.x,event.y) else 'fleur')

    def toggle_details(self):
        self.expanded=not self.expanded;self.settings['expanded']=self.expanded
        self._resize();self._draw();self.persist();self._follow()

    def persist(self):
        try:save_settings(self.state/'settings.json',self.settings)
        except (OSError,ValueError):
            self.warning='设置无法保存，当前设置仍在本次运行生效。';logging.exception('Settings save failed')

    def apply_settings(self):
        self.book=PriceBook(self.settings);self.persist();self._draw();self.position=None

    def reset_position(self):
        self.settings.update(right_inset=16,top_inset=64);self.persist();self.position=None

    def _context_menu(self,event=None):
        menu=tk.Menu(self.root,tearoff=False,bg=BG,fg=TEXT,activebackground='#243645',activeforeground=TEXT)
        menu.add_command(label='收起明细' if self.expanded else '展开明细',command=self.toggle_details)
        menu.add_command(label='价格 / 会话设置…',command=self.open_settings)
        menu.add_command(label='恢复右上角位置',command=self.reset_position)
        if self.settings['pinned_session_id']:
            def unpin():self.settings['pinned_session_id']=None;self.apply_settings()
            menu.add_command(label='取消锁定，自动跟随活跃会话',command=unpin)
        menu.add_separator();menu.add_command(label='退出用量条',command=self.quit)
        self.menu_open=True
        try:
            menu.tk_popup(event.x_root if event else self.root.winfo_pointerx(),event.y_root if event else self.root.winfo_pointery())
        finally:
            try:menu.grab_release()
            except tk.TclError:pass
            self.menu_open=False

    def open_settings(self):
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.lift();self.dialog.focus_force();return
        from .settings_ui import open_settings_dialog
        self.dialog=open_settings_dialog(self)

    def run(self):
        self.root.mainloop()

    def quit(self):
        if self.closed:return
        self.closed=True;self.stop.set()
        self.tray.close()
        if hasattr(self.native,'close'):self.native.close()
        self.root.destroy()

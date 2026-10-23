"""Run the actual settings save callback against an isolated app/state directory."""
import ctypes as C
from ctypes import wintypes as W
from decimal import Decimal
import json
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from codex_meter.native import configure_dpi_awareness
from codex_meter.instance import InstanceGuard
from codex_meter.settings import default_settings,load_settings
from codex_meter.app import UsageApp


def run():
    configure_dpi_awareness()
    user=C.WinDLL('user32',use_last_error=True)
    user.CreateWindowExW.argtypes=[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID]
    user.CreateWindowExW.restype=W.HWND
    user.DestroyWindow.argtypes=[W.HWND];user.DestroyWindow.restype=W.BOOL
    host=user.CreateWindowExW(0x08000080,'STATIC','Usage settings test host',0,0,0,900,500,None,None,None,None)
    assert host
    app=guard=None
    try:
        with tempfile.TemporaryDirectory(prefix='meter-settings-') as directory:
            state=Path(directory)
            assert state.resolve().parent==Path(tempfile.gettempdir()).resolve() and state.name.startswith('meter-settings-')
            guard=InstanceGuard(state)
            options=SimpleNamespace(codex_home=state/'empty',target_hwnd=int(host),allow_background=True,
                                    diagnostics=None,exit_after=0,expanded=False)
            app=UsageApp(options,state,default_settings(),'',guard)
            try:
                app.open_settings();dialog=app.dialog;dialog.update_idletasks()
                frame=dialog.winfo_children()[0]
                children=frame.winfo_children()
                custom=next(w for w in children if w.winfo_class()=='TCheckbutton' and '为这个模型' in str(w.cget('text')))
                custom.invoke()
                for row,value in ((5,'1.5'),(6,'0.15'),(7,'1.8'),(8,'6'),(11,'0.5')):
                    entry=next(w for w in children if w.winfo_class()=='TEntry' and int(w.grid_info()['row'])==row)
                    entry.delete(0,'end');entry.insert(0,value)
                currency=next(w for w in children if w.winfo_class()=='TCombobox' and int(w.grid_info()['row'])==4)
                currency.set('CNY')
                long_option=next(w for w in children if w.winfo_class()=='TCheckbutton' and '长上下文阶梯' in str(w.cget('text')))
                if 'selected' in long_option.state():long_option.invoke()
                buttons=next(w for w in children if w.winfo_class()=='TFrame')
                save=next(w for w in buttons.winfo_children() if str(w.cget('text'))=='保存并应用')
                save.invoke();app.root.update_idletasks()
                data,warning=load_settings(state/'settings.json')
                assert not warning,warning
                assert data['custom_rates']['gpt-6-astra']['currency']=='CNY'
                assert data['price_multiplier']==.5
                usage={'input_tokens':1000,'cached_input_tokens':200,'cache_write_input_tokens':300,'output_tokens':100,'total_tokens':1100}
                quote=app.book.quote('gpt-6-astra',usage)
                assert quote.amount==Decimal('0.000960'),quote
                assert quote.currency=='CNY'
                assert app.dialog is None
                return {'actual_save_callback':True,'custom_cny_roundtrip':True,'channel_multiplier':.5,
                        'quote_amount':str(quote.amount),'quote_currency':quote.currency}
            finally:
                if app:app.quit()
                guard.close();logging.shutdown()
    finally:
        user.DestroyWindow(host)

if __name__=='__main__':
    result=run();text=json.dumps(result,ensure_ascii=False,indent=2)
    print(text);(ROOT/'docs/evidence/settings-smoke.json').write_text(text+'\n',encoding='utf-8')

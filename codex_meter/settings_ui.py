"""User-activated settings dialog. Automatic refresh never opens this window."""
from decimal import Decimal,InvalidOperation
import tkinter as tk
from tkinter import ttk,messagebox

from .presentation import select_session,session_choices
from .pricing import normalize_model,validate_custom_rate


def open_settings_dialog(app):
    dialog=tk.Toplevel(app.root);dialog.title('Codex 用量条 · 单价与显示设置')
    dialog.configure(bg='#151c25');dialog.resizable(False,False)
    if app.visible:dialog.transient(app.root)
    style=ttk.Style(dialog);style.theme_use('clam')
    style.configure('Meter.TFrame',background='#151c25')
    style.configure('Meter.TLabel',background='#151c25',foreground='#e7eef5',font=('Microsoft YaHei UI',10))
    style.configure('Note.Meter.TLabel',foreground='#9cabbc',font=('Microsoft YaHei UI',9))
    style.configure('Title.Meter.TLabel',font=('Microsoft YaHei UI',14,'bold'))
    style.configure('Meter.TCheckbutton',background='#151c25',foreground='#e7eef5',font=('Microsoft YaHei UI',9))
    frame=ttk.Frame(dialog,style='Meter.TFrame',padding=20);frame.grid(sticky='nsew')
    frame.columnconfigure(1,weight=1)
    ttk.Label(frame,text='单价与显示',style='Title.Meter.TLabel').grid(row=0,column=0,columnspan=3,sticky='w')
    ttk.Label(frame,text='金额是 token 价格估算，不是平台实扣或订阅余额。\n第三方渠道请启用自定义单价；不读取 API Key，也不会联网取账单。',
              style='Note.Meter.TLabel',wraplength=570).grid(row=1,column=0,columnspan=3,sticky='w',pady=(8,16))
    session=select_session(app.snapshot,app.settings['pinned_session_id']) if app.snapshot else None
    models=set(app.book.reference_rates)|set(app.settings['custom_rates'])
    if app.snapshot:models.update(normalize_model(s.model) for s in app.snapshot.sessions)
    models.discard('unknown');models.add(normalize_model(session.model) if session else 'gpt-6-astra')
    selected=tk.StringVar(value=normalize_model(session.model) if session else 'gpt-6-astra')
    ttk.Label(frame,text='模型',style='Meter.TLabel').grid(row=2,column=0,sticky='w')
    model_box=ttk.Combobox(frame,textvariable=selected,values=sorted(models),width=35)
    model_box.grid(row=2,column=1,columnspan=2,sticky='ew',pady=4)
    custom=tk.BooleanVar();apply_long=tk.BooleanVar();currency=tk.StringVar(value='USD')
    prices={key:tk.StringVar() for key in ('input','cached','cache_write','output')}
    note=tk.StringVar()
    ttk.Checkbutton(frame,text='为这个模型启用自定义单价',variable=custom,style='Meter.TCheckbutton').grid(row=3,column=0,columnspan=3,sticky='w',pady=8)
    ttk.Label(frame,text='币种',style='Meter.TLabel').grid(row=4,column=0,sticky='w')
    ttk.Combobox(frame,textvariable=currency,values=('USD','CNY'),width=12,state='readonly').grid(row=4,column=1,sticky='w')
    ttk.Label(frame,text='USD = 美元；CNY = 人民币',style='Note.Meter.TLabel').grid(row=4,column=2,sticky='w')
    for row,(key,label) in enumerate((('input','非缓存输入'),('cached','缓存读取'),('cache_write','缓存写入'),('output','输出（含推理）')),5):
        ttk.Label(frame,text=label,style='Meter.TLabel').grid(row=row,column=0,sticky='w',padx=(0,20),pady=5)
        ttk.Entry(frame,textvariable=prices[key],width=18).grid(row=row,column=1,sticky='w')
        ttk.Label(frame,text='每 1,000,000 tokens',style='Note.Meter.TLabel').grid(row=row,column=2,sticky='w')
    ttk.Checkbutton(frame,text='沿用此模型参考价的长上下文阶梯（若有）',variable=apply_long,
                    style='Meter.TCheckbutton').grid(row=9,column=0,columnspan=3,sticky='w',pady=(6,0))
    ttk.Label(frame,textvariable=note,style='Note.Meter.TLabel',wraplength=570).grid(row=10,column=0,columnspan=3,sticky='w',pady=(5,10))
    multiplier=tk.StringVar(value=str(app.settings['price_multiplier']))
    ttk.Label(frame,text='渠道倍率',style='Meter.TLabel').grid(row=11,column=0,sticky='w')
    ttk.Entry(frame,textvariable=multiplier,width=18).grid(row=11,column=1,sticky='w')
    ttk.Label(frame,text='默认 1；按你实际渠道折扣填写',style='Note.Meter.TLabel').grid(row=11,column=2,sticky='w')
    reference=tk.BooleanVar(value=app.settings['use_reference_prices'])
    ttk.Checkbutton(frame,text='没有自定义价格时，允许使用内置 API 参考价（美元）',variable=reference,
                    style='Meter.TCheckbutton').grid(row=12,column=0,columnspan=3,sticky='w',pady=10)
    ttk.Separator(frame).grid(row=13,column=0,columnspan=3,sticky='ew',pady=8)
    choices=session_choices(app.snapshot,app.settings['pinned_session_id'])
    current=next((label for label,sid in choices.items() if sid==app.settings['pinned_session_id']),next(iter(choices)))
    pin=tk.StringVar(value=current)
    ttk.Label(frame,text='统计会话',style='Meter.TLabel').grid(row=14,column=0,sticky='w')
    ttk.Combobox(frame,textvariable=pin,values=list(choices),state='readonly',width=47).grid(row=14,column=1,columnspan=2,sticky='ew',pady=6)
    ttk.Label(frame,text='自动模式识别最近活跃的日志，不读取桌面端当前选中哪个聊天。\n今日累计包含本机各会话和子代理；今日按 UTC+8 划分。',
              style='Note.Meter.TLabel',wraplength=570).grid(row=15,column=0,columnspan=3,sticky='w',pady=(5,10))
    buttons=ttk.Frame(frame,style='Meter.TFrame');buttons.grid(row=16,column=0,columnspan=3,sticky='ew',pady=(8,0))

    def populate(*_):
        model=normalize_model(selected.get().strip())
        default=app.book.reference_rates.get(model,{})
        own=app.settings['custom_rates'].get(model)
        rate=own if own is not None else default
        custom.set(own is not None);currency.set(rate.get('currency','USD'))
        for key in prices:prices[key].set(str(rate.get(key,0)))
        apply_long.set(bool(rate.get('long_context_threshold')))
        threshold=default.get('long_context_threshold')
        extra=f'单次输入超过 {threshold:,} 时采用参考阶梯。' if threshold else '此模型没有内置长上下文阶梯。'
        note.set('参考价快照：2026-10-23。币种切换不会自动换算。\n'+extra)
    model_box.bind('<<ComboboxSelected>>',populate)
    model_box.bind('<FocusOut>',populate)

    def save():
        try:
            model=normalize_model(selected.get().strip())
            if not model or len(model)>256:raise ValueError('请填写有效模型名称')
            factor=Decimal(multiplier.get())
            if not factor.is_finite() or not 0<=factor<=10000:raise ValueError('渠道倍率必须在 0 到 10000 之间')
            if custom.get():
                rate={'currency':currency.get()}
                for key,var in prices.items():
                    number=Decimal(var.get())
                    if not number.is_finite() or not 0<=number<=1_000_000_000:
                        raise ValueError('单价必须是 0 到 1000000000 之间的有限数字')
                    rate[key]=float(number)
                reference_rate=app.book.reference_rates.get(model,{})
                rate.update(long_context_threshold=reference_rate.get('long_context_threshold') if apply_long.get() else None,
                            long_input_multiplier=reference_rate.get('long_input_multiplier',1) if apply_long.get() else 1,
                            long_output_multiplier=reference_rate.get('long_output_multiplier',1) if apply_long.get() else 1)
                validated=validate_custom_rate(rate)
                app.settings['custom_rates'][model]=validated
            else:app.settings['custom_rates'].pop(model,None)
            app.settings['price_multiplier']=float(factor)
            app.settings['use_reference_prices']=reference.get()
            app.settings['pinned_session_id']=choices.get(pin.get())
            app.apply_settings();dialog.destroy();app.dialog=None
        except (ValueError,InvalidOperation,TypeError) as error:
            messagebox.showerror('设置未保存',str(error),parent=dialog)

    def reset():
        model=normalize_model(selected.get().strip())
        app.settings['custom_rates'].pop(model,None)
        app.settings['use_reference_prices']=True;reference.set(True)
        app.apply_settings();populate()
    ttk.Button(buttons,text='恢复此模型参考价',command=reset).pack(side='left')
    ttk.Button(buttons,text='恢复位置',command=app.reset_position).pack(side='left',padx=8)
    ttk.Button(buttons,text='保存并应用',command=save).pack(side='right')
    ttk.Button(buttons,text='关闭',command=dialog.destroy).pack(side='right',padx=8)
    populate();dialog.update_idletasks()
    width=dialog.winfo_reqwidth();height=dialog.winfo_reqheight()
    if app.host:
        area=app.native.work_area(app.host.hwnd)
        x=max(area.left,min(app.host.rect.left+(app.host.rect.width-width)//2,area.right-width))
        y=max(area.top,min(app.host.rect.top+80,area.bottom-height))
        dialog.geometry(f'+{x}+{y}')
    dialog.lift();dialog.focus_set()
    return dialog

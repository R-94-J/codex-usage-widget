"""Local, atomic settings; no authentication configuration is read."""
import json
import math
import os
import tempfile
from pathlib import Path


def default_settings():
    return {'schema_version':1,'right_inset':16,'top_inset':64,'expanded':False,
            'poll_seconds':2,'utc_offset_minutes':480,'pinned_session_id':None,
            'custom_rates':{},'price_multiplier':1.0,'use_reference_prices':True}


def default_state_dir():
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share')
    return base/'CodexUsageWidget'


def validate_settings(raw):
    if not isinstance(raw,dict):
        raise ValueError('设置必须是 JSON 对象')
    result=default_settings()
    for key,value in raw.items():
        if key not in result:
            continue
        if key in ('right_inset','top_inset','utc_offset_minutes','schema_version'):
            if isinstance(value,bool) or not isinstance(value,int):
                raise ValueError(f'{key} 必须是整数')
            low,high=(-720,840) if key=='utc_offset_minutes' else (0,10000)
            if not low<=value<=high:
                raise ValueError(f'{key} 超出范围')
        elif key in ('expanded','use_reference_prices'):
            if not isinstance(value,bool):raise ValueError(f'{key} 必须是布尔值')
        elif key in ('poll_seconds','price_multiplier'):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
                raise ValueError(f'{key} 必须是有限数字')
            low,high=(0.5,60) if key=='poll_seconds' else (0,10000)
            if not low<=value<=high:raise ValueError(f'{key} 超出范围')
        elif key=='pinned_session_id':
            if value is not None and (not isinstance(value,str) or len(value)>256):
                raise ValueError('会话标识无效')
        elif key=='custom_rates':
            if not isinstance(value,dict) or len(value)>500:
                raise ValueError('自定义单价格式无效')
            # Rate semantics are validated by the pricing engine and settings dialog.
            for model,rate in value.items():
                if not isinstance(model,str) or len(model)>256 or not isinstance(rate,dict):
                    raise ValueError('自定义单价条目无效')
        result[key]=value
    # Keep semantic validation inside load_settings' recovery boundary.
    # In particular, duplicate route-normalized names must not silently collide.
    from .pricing import PriceBook,normalize_model,validate_custom_rate
    PriceBook(result)
    result['custom_rates']={normalize_model(model):validate_custom_rate(rate)
                            for model,rate in result['custom_rates'].items()}
    return result


def load_settings(path):
    path=Path(path)
    if not path.exists():return default_settings(),''
    try:
        if path.stat().st_size>1024*1024:raise ValueError('设置文件过大')
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        return validate_settings(data),''
    except (OSError,ValueError,UnicodeError):
        return default_settings(),'设置文件无法读取，暂用默认值；原文件未改动。'


def save_settings(path,data):
    data=validate_settings(data)
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='settings-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:
            json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
            f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

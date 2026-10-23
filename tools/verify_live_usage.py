"""Freeze count-only metadata, compare the collector with an independent last-call oracle."""
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from codex_meter.usage import UsageCollector
HOME=Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')
ZONE=timezone(timedelta(hours=8));NOW=datetime.now(ZONE)
KEYS=('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens')


def run():
    seen=set();totals={k:0 for k in KEYS};files=0;rows=0;calls=0;rawbytes=0
    previous_by_session={};resets=0
    with tempfile.TemporaryDirectory(prefix='usage-verification-') as directory:
        target=Path(directory)
        assert target.resolve().parent==Path(tempfile.gettempdir()).resolve() and target.name.startswith('usage-verification-')
        for folder in ('sessions','archived_sessions'):
            for path in sorted((HOME/folder).rglob('*.jsonl')):
                sid=path.stem;safe=[];files+=1
                size=path.stat().st_size;rawbytes+=size
                with path.open('rb') as stream:
                    while stream.tell()<size:
                        line=stream.readline(size-stream.tell())
                        if not line.endswith(b'\n'):break
                        head=line[:180]
                        if not any(word in head for word in (b'"session_meta"',b'"turn_context"',b'"event_msg"')):continue
                        if b'"event_msg"' in head and b'"token_count"' not in line[:650]:continue
                        try:record=json.loads(line)
                        except ValueError:continue
                        payload=record.get('payload',{});kind=record.get('type')
                        if kind=='session_meta':
                            sid=payload.get('id',sid)
                            record['payload']={k:payload[k] for k in ('id','timestamp','source','originator','model_provider','forked_from_id') if k in payload}
                        elif kind=='turn_context':
                            record['payload']={k:payload[k] for k in ('model','model_provider','service_tier','turn_id') if k in payload}
                        elif kind=='event_msg' and payload.get('type')=='token_count':
                            info=payload.get('info') or {};total=info.get('total_token_usage');last=info.get('last_token_usage')
                            record['payload']={'type':'token_count','info':{'total_token_usage':total,'last_token_usage':last}}
                            if total and last:
                                vector=tuple(total.get(k,0) for k in KEYS)
                                before=previous_by_session.get(sid)
                                if before and (vector[0]<before[0] or vector[3]<before[3]):resets+=1
                                previous_by_session[sid]=vector
                                key=(sid,vector)
                                timestamp=datetime.fromisoformat(record['timestamp'].replace('Z','+00:00'))
                                if key not in seen and timestamp.astimezone(ZONE).date()==NOW.date():
                                    for k in KEYS:totals[k]+=last.get(k,0)
                                    calls+=1
                                seen.add(key)
                        else:continue
                        safe.append(record);rows+=1
                output=target/folder/path.name;output.parent.mkdir(parents=True,exist_ok=True)
                output.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in safe),encoding='utf-8')
        snapshot=UsageCollector(target).poll(NOW)
        actual={k:sum(e.usage[k] for e in snapshot.today_events) for k in KEYS}
        result={'checked_at':NOW.isoformat(),'source_files':files,'source_bytes':rawbytes,'metadata_records':rows,
                'today_distinct_calls':calls,'counter_resets_observed':resets,
                'independent_last_call_totals':totals,'collector_totals':actual,
                'today_tokens':snapshot.today_tokens,'errors':list(snapshot.errors),'match':totals==actual}
        assert resets==0,'Independent oracle is intentionally limited to monotonic real traces'
        assert result['match'],json.dumps(result)
        return result

if __name__=='__main__':
    result=run();text=json.dumps(result,ensure_ascii=False,indent=2)
    print(text);(ROOT/'docs/evidence/live-usage-verification.json').write_text(text+'\n',encoding='utf-8')

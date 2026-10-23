import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from codex_meter.usage import UsageCollector
except ImportError:
    UsageCollector = None

ZONE = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 23, 14, 0, tzinfo=ZONE)


def counts(i=100, o=20, cached=0, write=0, reasoning=0):
    return dict(input_tokens=i, cached_input_tokens=cached,
                cache_write_input_tokens=write, output_tokens=o,
                reasoning_output_tokens=reasoning, total_tokens=i+o)


def meta(sid='s1', ts='2026-10-23T01:00:00Z', **extra):
    return dict(timestamp=ts, type='session_meta', payload={
        'id': sid, 'timestamp': ts, 'originator': 'Codex Desktop',
        'source': 'vscode', 'model_provider': 'custom', **extra})


def context(model='gpt-6-astra', ts='2026-10-23T01:00:01Z', **extra):
    return dict(timestamp=ts, type='turn_context', payload={
        'model': model, 'turn_id': 'turn-1', **extra})


def event(total=None, last=None, ts='2026-10-23T01:00:02Z'):
    return dict(timestamp=ts, type='event_msg', payload={
        'type': 'token_count', 'info': {
            'total_token_usage': total, 'last_token_usage': last}})


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(UsageCollector, 'incremental usage collector is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home/'sessions/2026/10/23/session.jsonl'
        self.path.parent.mkdir(parents=True)
        self.collector = UsageCollector(self.home)

    def write(self, *rows, path=None, append=False):
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a' if append else 'w', encoding='utf-8', newline='\n') as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False)+'\n')

    def poll(self):
        return self.collector.poll(NOW)

    def test_input_and_output_only_form_token_total(self):
        u=counts(1000, 200, 300, 400, 100)
        self.write(meta(), context(), event(u,u))
        s=self.poll()
        self.assertEqual(s.today_tokens, 1200)
        self.assertEqual(s.sessions[0].total_tokens,1200)
        self.assertEqual(s.sessions[0].events[0].usage['cache_write_input_tokens'],400)

    def test_repeated_cumulative_updates_do_not_double_count(self):
        u=counts(); v=counts(300,60)
        self.write(meta(),context(),event(u,u),event(u,u,ts='2026-10-23T01:00:03Z'),
                   event(v,counts(200,40),ts='2026-10-23T01:00:04Z'))
        s=self.poll()
        self.assertEqual(s.today_tokens,360)
        self.assertEqual(len(s.sessions[0].events),2)

    def test_incremental_reader_only_reads_appended_content(self):
        u=counts(); self.write(meta(),context(),event(u,u))
        self.assertGreater(self.poll().bytes_read,0)
        self.assertEqual(self.poll().bytes_read,0)
        self.write(event(counts(200,40),u,ts='2026-10-23T01:00:03Z'),append=True)
        s=self.poll()
        self.assertEqual(s.today_tokens,240)
        self.assertLess(s.bytes_read,self.path.stat().st_size)

    def test_partial_utf8_line_waits_for_newline(self):
        self.write(meta(),context())
        raw=json.dumps(event(counts(),counts()),ensure_ascii=False).encode('utf-8')
        with self.path.open('ab') as f:f.write(raw[:40])
        self.assertEqual(self.poll().today_tokens,0)
        with self.path.open('ab') as f:f.write(raw[40:]+b'\n')
        self.assertEqual(self.poll().today_tokens,120)

    def test_bad_json_and_irrelevant_conversation_are_not_interpreted(self):
        self.write(meta(),context())
        with self.path.open('a',encoding='utf-8') as f:f.write('{"type":"event_msg",broken\n')
        self.write({'type':'response_item','payload':{'text':'secret token_count fake input_tokens 99999'}},
                   event(counts(),counts()),append=True)
        s=self.poll()
        self.assertEqual(s.today_tokens,120)
        self.assertTrue(s.errors)
        self.assertNotIn('secret',str(s))

    def test_same_session_copied_into_archive_is_deduplicated(self):
        rows=[meta(),context(),event(counts(),counts())]
        self.write(*rows)
        self.write(*rows,path=self.home/'archived_sessions/copy.jsonl')
        s=self.poll()
        self.assertEqual(len(s.sessions),1)
        self.assertEqual(s.today_tokens,120)

    def test_archive_move_preserves_usage(self):
        self.write(meta(),context(),event(counts(),counts()))
        self.poll()
        target=self.home/'archived_sessions/moved.jsonl';target.parent.mkdir()
        self.path.rename(target)
        self.assertEqual(self.poll().today_tokens,120)

    def test_truncation_rebuilds_only_that_file(self):
        self.write(meta(),context(),event(counts(999,99),counts(999,99)))
        self.poll()
        self.write(meta(),context(),event(counts(),counts()))
        self.assertEqual(self.poll().today_tokens,120)

    def test_same_size_rewrite_is_detected(self):
        self.write(meta(),context(),event(counts(100,20),counts(100,20)))
        self.poll()
        before=self.path.stat().st_size
        self.write(meta(),context(),event(counts(200,30),counts(200,30)))
        self.assertEqual(self.path.stat().st_size,before)
        self.assertEqual(self.poll().today_tokens,230)

    def test_utc_plus_eight_day_boundary(self):
        self.write(meta(ts='2026-10-22T15:00:00Z'),context(),
                   event(counts(),counts(),ts='2026-10-22T15:59:59Z'),
                   event(counts(200,40),counts(),ts='2026-10-22T16:00:00Z'))
        s=self.poll()
        self.assertEqual(s.today_tokens,120)
        self.assertEqual(s.sessions[0].total_tokens,240)

    def test_restarted_session_today_does_not_charge_yesterday_again(self):
        self.write(meta(ts='2026-10-22T10:00:00Z'),context(),
                   event(counts(1000,200),counts(1000,200),ts='2026-10-22T10:00:02Z'),
                   event(counts(1100,220),counts(),ts='2026-10-23T01:00:00Z'))
        self.assertEqual(self.poll().today_tokens,120)

    def test_missing_initial_history_becomes_undated_baseline(self):
        self.write(meta(),context(),event(counts(1000,200),counts()))
        s=self.poll(); es=s.sessions[0].events
        self.assertEqual(s.sessions[0].total_tokens,1200)
        self.assertEqual(s.today_tokens,120)
        self.assertEqual(sum(e.usage['total_tokens'] for e in es if e.timestamp is None),1080)
        self.assertTrue(any(not e.precise for e in es))

    def test_missing_request_detail_is_not_priced_as_single_long_request(self):
        self.write(meta(),context(),event(counts(),counts()),
                   event(counts(400100,60),counts(100,20),ts='2026-10-23T01:00:03Z'))
        es=self.poll().sessions[0].events
        self.assertEqual(es[-1].usage['input_tokens'],400000)
        self.assertFalse(es[-1].precise)

    def test_counter_reset_preserves_observed_before_and_after(self):
        self.write(meta(),context(),event(counts(500,100),counts(500,100)),
                   event(counts(),counts(),ts='2026-10-23T02:00:00Z'))
        self.assertEqual(self.poll().today_tokens,720)

    def test_model_and_tier_are_attached_to_each_request(self):
        self.write(meta(),context(service_tier='fast'),event(counts(),counts()),
                   context('gpt-6.1-sol',ts='2026-10-23T02:00:00Z',service_tier='flex'),
                   event(counts(200,40),counts(),ts='2026-10-23T02:00:01Z'))
        es=self.poll().sessions[0].events
        self.assertEqual([(e.model,e.service_tier) for e in es],
                         [('gpt-6-astra','fast'),('gpt-6.1-sol','flex')])

    def test_subagent_is_identified_but_its_usage_is_included_today(self):
        self.write(meta(source={'subagent':{'thread_spawn':{'parent_thread_id':'parent'}}}),
                   context(),event(counts(),counts()))
        s=self.poll()
        self.assertTrue(s.sessions[0].is_subagent)
        self.assertEqual(s.today_tokens,120)

    def test_fork_does_not_count_copied_parent_history(self):
        parent=[meta('parent',ts='2026-10-23T00:00:00Z'),context(),event(counts(),counts())]
        self.write(*parent)
        self.write(meta('child',ts='2026-10-23T02:00:00Z',forked_from_id='parent'),context(),
                   event(counts(),counts()),context(ts='2026-10-23T02:00:01Z'),
                   event(counts(200,40),counts(),ts='2026-10-23T02:00:02Z'),
                   path=self.home/'sessions/child.jsonl')
        s=self.poll()
        self.assertEqual(s.today_tokens,240)
        child=next(x for x in s.sessions if x.id=='child')
        self.assertEqual(child.total_tokens,120)

    def test_fork_without_copied_lines_does_not_charge_inherited_baseline(self):
        self.write(meta('child',forked_from_id='parent'),context(),event(counts(1000,200),counts()))
        s=self.poll()
        self.assertEqual(s.today_tokens,120)
        self.assertEqual(s.sessions[0].total_tokens,120)

    def test_null_info_rate_limit_event_is_ignored(self):
        self.write(meta(),context(),dict(type='event_msg',payload={'type':'token_count','info':None}),
                   event(counts(),counts()))
        self.assertEqual(self.poll().today_tokens,120)

    def test_absent_cumulative_uses_timestamped_last_usage_once(self):
        row=event(None,counts());self.write(meta(),context(),row,row)
        self.assertEqual(self.poll().today_tokens,120)

    def test_invalid_negative_usage_is_rejected(self):
        self.write(meta(),context(),event(counts(-50,20),counts(-50,20)))
        s=self.poll()
        self.assertEqual(s.today_tokens,0)
        self.assertTrue(s.errors)

    def test_missing_home_is_empty_not_fatal(self):
        s=UsageCollector(self.home/'missing').poll(NOW)
        self.assertEqual(s.today_tokens,0)
        self.assertEqual(s.files_scanned,0)


    def test_replayed_old_record_cannot_roll_back_cumulative_checkpoint(self):
        a=event(counts(100,0),counts(100,0),ts='2026-10-23T01:00:02Z')
        b=event(counts(200,0),counts(100,0),ts='2026-10-23T01:00:03Z')
        c=event(counts(300,0),counts(100,0),ts='2026-10-23T01:00:04Z')
        self.write(meta(),context(),a,b,a,c)
        self.assertEqual(self.poll().today_tokens,300)

    def test_last_only_usage_is_reconciled_when_cumulative_resumes(self):
        self.write(meta(),context(),event(counts(100,0),counts(100,0)),
                   event(None,counts(50,0),ts='2026-10-23T01:00:03Z'),
                   event(counts(200,0),counts(50,0),ts='2026-10-23T01:00:04Z'))
        s=self.poll()
        self.assertEqual(s.today_tokens,200)
        self.assertTrue(all(e.precise for e in s.sessions[0].events))

    def test_fork_initial_snapshot_without_last_details_excludes_inherited_tokens(self):
        self.write(meta('child',forked_from_id='parent'),context(),
                   event(counts(1000,0),None),
                   event(counts(1100,0),counts(100,0),ts='2026-10-23T01:00:03Z'))
        self.assertEqual(self.poll().sessions[0].total_tokens,100)

    def test_first_cumulative_does_not_double_count_earlier_last_only_call(self):
        self.write(meta(),context(),event(None,counts(100,0)),
                   event(counts(150,0),counts(50,0),ts='2026-10-23T01:00:03Z'))
        s=self.poll()
        self.assertEqual(s.sessions[0].total_tokens,150)
        self.assertEqual(s.today_tokens,150)

    def test_repeated_cumulative_between_last_only_and_catchup_keeps_pending_usage(self):
        self.write(meta(),context(),event(counts(100,0),counts(100,0)),
                   event(None,counts(50,0),ts='2026-10-23T01:00:03Z'),
                   event(counts(100,0),counts(100,0),ts='2026-10-23T01:00:04Z'),
                   event(counts(200,0),counts(50,0),ts='2026-10-23T01:00:05Z'))
        self.assertEqual(self.poll().today_tokens,200)


    def test_last_only_call_after_reset_is_not_reintroduced_as_baseline(self):
        self.write(meta(),context(),event(counts(100,0),counts(100,0)),
                   event(None,counts(20,0),ts='2026-10-23T01:00:03Z'),
                   event(counts(30,0),counts(10,0),ts='2026-10-23T01:00:04Z'))
        s=self.poll()
        self.assertEqual(s.sessions[0].total_tokens,130)
        self.assertEqual(s.today_tokens,130)

    def test_pending_call_before_reset_does_not_subtract_new_epoch_last_request(self):
        self.write(meta(),context(),event(counts(100,0),counts(100,0)),
                   event(None,counts(20,0),ts='2026-10-23T01:00:03Z'),
                   event(counts(30,0),counts(30,0),ts='2026-10-23T01:00:04Z'))
        self.assertEqual(self.poll().sessions[0].total_tokens,150)

if __name__=='__main__':unittest.main()

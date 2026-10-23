import unittest
from datetime import datetime,timezone
from decimal import Decimal
from codex_meter.usage import UsageEvent,SessionUsage,UsageSnapshot
try:
    from codex_meter.presentation import format_tokens,format_money,select_session,summarize_cost
except ImportError:
    format_tokens=format_money=select_session=summarize_cost=None

NOW=datetime(2026,10,23,tzinfo=timezone.utc)

def session(sid,sub=False):
    return SessionUsage(sid,'gpt-6-astra','custom',NOW,NOW,sub,())

def ev(model='gpt-6-astra',precise=True):
    return UsageEvent('s1',NOW,model,'custom',None,
                     {'input_tokens':1000,'cached_input_tokens':200,'cache_write_input_tokens':300,
                      'output_tokens':100,'reasoning_output_tokens':50,'total_tokens':1100},precise,'x')

class PresentationTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(format_tokens,'presentation implementation is missing')

    def test_token_format_is_decimal_not_binary(self):
        for n,text in ((0,'0'),(999,'999'),(1000,'1.00K'),(123456,'123.5K'),(1000000,'1.00M'),(1234567890,'1.23B')):
            self.assertEqual(format_tokens(n),text)

    def test_money_does_not_disguise_tiny_nonzero_usage_as_zero(self):
        self.assertEqual(format_money(Decimal('0.000001'),'USD'),'<$0.0001')
        self.assertEqual(format_money(Decimal('0'),'USD'),'$0.00')
        self.assertEqual(format_money(Decimal('0.01395'),'CNY'),'¥0.0140')

    def test_auto_selection_ignores_newer_subagent(self):
        s=UsageSnapshot((session('child',True),session('main')),(),NOW,(),2,0)
        self.assertEqual(select_session(s,None).id,'main')

    def test_explicit_pin_is_honored_and_missing_pin_is_not_replaced(self):
        s=UsageSnapshot((session('s2'),session('s1')),(),NOW,(),2,0)
        self.assertEqual(select_session(s,'s1').id,'s1')
        self.assertIsNone(select_session(s,'deleted'))

    def test_unknown_price_is_not_zero_dollars(self):
        from codex_meter.pricing import PriceBook
        c=summarize_cost([ev('unknown-model')],PriceBook())
        self.assertEqual(c.display,'未定价')
        self.assertEqual(c.unpriced_events,1)

    def test_precise_and_partial_cost_labels(self):
        from codex_meter.pricing import PriceBook
        c=summarize_cost([ev()],PriceBook())
        self.assertEqual(c.amounts['USD'],Decimal('0.01395'))
        self.assertEqual(c.display,'$0.0140')
        c=summarize_cost([ev(),ev('unknown')],PriceBook())
        self.assertEqual(c.display,'≥$0.0140')
        self.assertEqual(c.unpriced_events,1)

    def test_currencies_are_not_added_together(self):
        from codex_meter.pricing import PriceBook
        book=PriceBook({'custom_rates':{'custom':{'input':1,'cached':1,'cache_write':1,'output':1,'currency':'CNY'}}})
        c=summarize_cost([ev(),ev('custom')],book)
        self.assertIn('USD',c.amounts);self.assertIn('CNY',c.amounts)
        self.assertIn(' + ',c.display)

    def test_empty_period_is_zero_not_unpriced(self):
        from codex_meter.pricing import PriceBook
        self.assertEqual(summarize_cost([],PriceBook()).display,'$0.00')


    def test_session_choices_preserve_pin_beyond_recent_limit(self):
        import codex_meter.presentation as ui
        self.assertTrue(hasattr(ui,'session_choices'),'shared session choices are missing')
        sessions=tuple(session(f's{i}') for i in range(102))
        snap=UsageSnapshot(sessions,(),NOW,(),102,0)
        choices=ui.session_choices(snap,'s101')
        self.assertIn('s101',choices.values())

    def test_session_choices_preserve_pin_with_missing_log(self):
        import codex_meter.presentation as ui
        self.assertTrue(hasattr(ui,'session_choices'),'shared session choices are missing')
        snap=UsageSnapshot((session('recent'),),(),NOW,(),1,0)
        self.assertIn('missing',ui.session_choices(snap,'missing').values())

if __name__=='__main__':unittest.main()

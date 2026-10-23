import json
import tempfile
import unittest
from pathlib import Path

try:
    from codex_meter.settings import default_settings, load_settings, save_settings, validate_settings
except ImportError:
    default_settings=load_settings=save_settings=validate_settings=None

class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(default_settings,'settings implementation is missing')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'settings.json'

    def test_defaults_are_offline_and_not_autostart(self):
        data=default_settings()
        self.assertEqual(data['poll_seconds'],2)
        self.assertEqual(data['right_inset'],16)
        self.assertEqual(data['top_inset'],64)
        self.assertEqual(data['utc_offset_minutes'],480)
        self.assertNotIn('autostart',data)
        self.assertEqual(data['custom_rates'],{})

    def test_missing_settings_uses_defaults_without_writing(self):
        data,warning=load_settings(self.path)
        self.assertEqual(data,default_settings());self.assertFalse(warning)
        self.assertFalse(self.path.exists())

    def test_roundtrip_is_atomic_and_keeps_unicode(self):
        data=default_settings();data['pinned_session_id']='会话-123'
        save_settings(self.path,data)
        loaded,warning=load_settings(self.path)
        self.assertEqual(loaded,data);self.assertFalse(warning)
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])

    def test_corrupted_json_is_not_overwritten_on_load(self):
        self.path.write_text('broken settings',encoding='utf-8')
        data,warning=load_settings(self.path)
        self.assertTrue(warning);self.assertEqual(data,default_settings())
        self.assertEqual(self.path.read_text(),'broken settings')

    def test_wrong_types_and_nonfinite_numbers_are_rejected(self):
        for key,value in [('price_multiplier',float('nan')),('price_multiplier',-1),
                          ('poll_seconds',0),('right_inset',True),('pinned_session_id',12),
                          ('custom_rates',[]),('use_reference_prices','yes')]:
            with self.subTest(key=key,value=value):
                with self.assertRaises(ValueError):validate_settings({key:value})

    def test_unknown_keys_are_not_persisted(self):
        data=validate_settings({'api_key':'not-a-real-key','top_inset':80})
        self.assertNotIn('api_key',data);self.assertEqual(data['top_inset'],80)

    def test_unreasonable_bounds_are_rejected(self):
        for item in ({'poll_seconds':1000},{'utc_offset_minutes':2000},{'top_inset':-1}):
            with self.assertRaises(ValueError):validate_settings(item)


    def test_invalid_rate_semantics_recover_before_pricebook_startup(self):
        from codex_meter.pricing import PriceBook
        text=json.dumps({'custom_rates':{'gpt-6-astra':{}}})
        self.path.write_text(text,encoding='utf-8')
        data,warning=load_settings(self.path)
        self.assertTrue(warning)
        PriceBook(data)
        self.assertEqual(self.path.read_text(encoding='utf-8'),text)

    def test_duplicate_normalized_rate_keys_are_recovered(self):
        rate={'input':1,'cached':1,'cache_write':1,'output':1,'currency':'USD'}
        self.path.write_text(json.dumps({'custom_rates':{'gpt-6-astra':rate,'route/gpt-6-astra':rate}}),encoding='utf-8')
        data,warning=load_settings(self.path)
        self.assertTrue(warning)
        self.assertEqual(data['custom_rates'],{})

    def test_saved_custom_rate_keys_are_normalized_for_ui_lookup(self):
        rate={'input':1,'cached':1,'cache_write':1,'output':1,'currency':'USD'}
        data=validate_settings({'custom_rates':{'route/gpt-6-astra':rate}})
        self.assertIn('gpt-6-astra',data['custom_rates'])
        self.assertNotIn('route/gpt-6-astra',data['custom_rates'])

if __name__=='__main__':unittest.main()

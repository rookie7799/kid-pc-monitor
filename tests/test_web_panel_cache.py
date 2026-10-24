"""Tests for the panel's per-PC status cache.

The cache exists so a page render does not block on a fresh TCP round-trip per
field per PC. Run with `python -m unittest discover -s tests` or pytest; no
network or Windows needed (every agent call is patched).
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import web_panel  # noqa: E402

IP = '192.0.2.10'


def _fake_fetcher(value, calls):
    def fetch(ip, port=9999):
        calls.append(ip)
        return value
    return fetch


class StatusCacheTests(unittest.TestCase):
    def setUp(self):
        web_panel.status_cache.clear()
        web_panel.discovered_pcs.clear()
        web_panel.discovered_pcs[IP] = {'hostname': 'PC', 'status': 'online'}

    def tearDown(self):
        web_panel.status_cache.clear()
        web_panel.discovered_pcs.clear()

    def test_second_render_serves_cached_values_without_new_round_trips(self):
        calls = {name: [] for name in
                 ('status', 'current_user', 'usage_limit',
                  'time_remaining', 'lock_times')}
        with patch.object(web_panel, 'check_pc_status', _fake_fetcher('LOCKED', calls['status'])), \
             patch.object(web_panel, 'get_current_user', _fake_fetcher('Kids', calls['current_user'])), \
             patch.object(web_panel, 'get_usage_limit', _fake_fetcher(240, calls['usage_limit'])), \
             patch.object(web_panel, 'get_time_remaining', _fake_fetcher('120', calls['time_remaining'])), \
             patch.object(web_panel, 'get_lock_times', _fake_fetcher(None, calls['lock_times'])), \
             patch.object(web_panel, 'get_allowed_window', _fake_fetcher(None, []), create=True), \
             patch.object(web_panel, 'get_unlock_status', _fake_fetcher('INACTIVE', []), create=True):
            first = web_panel._pc_details(IP)
            second = web_panel._pc_details(IP)

        self.assertEqual(first, second)
        self.assertEqual(second['status'], 'LOCKED')
        self.assertEqual(second['usage_limit'], 240)
        for name, seen in calls.items():
            self.assertEqual(len(seen), 1, f'{name} fetched {len(seen)} times')

    def test_stale_entry_is_refreshed_and_errors_keep_the_previous_value(self):
        web_panel.status_cache[IP] = {
            'values': {'status': 'UNLOCKED', 'current_user': 'Kids',
                       'usage_limit': 240, 'time_remaining': '120',
                       'lock_times': ['21:00']},
            'ts': time.monotonic() - web_panel.STATUS_CACHE_TTL - 1,
        }
        with patch.object(web_panel, 'check_pc_status', _fake_fetcher('LOCKED', [])), \
             patch.object(web_panel, 'get_current_user', _fake_fetcher(None, [])), \
             patch.object(web_panel, 'get_usage_limit', _fake_fetcher(None, [])), \
             patch.object(web_panel, 'get_time_remaining', _fake_fetcher(None, [])), \
             patch.object(web_panel, 'get_lock_times', _fake_fetcher(None, [])), \
             patch.object(web_panel, 'get_allowed_window', _fake_fetcher(None, []), create=True), \
             patch.object(web_panel, 'get_unlock_status', _fake_fetcher('INACTIVE', []), create=True):
            details = web_panel._pc_details(IP)

        self.assertEqual(details['status'], 'LOCKED')
        # A transient error (None) must not blank a field the panel still shows.
        self.assertEqual(details['current_user'], 'Kids')
        self.assertEqual(details['lock_times'], ['21:00'])

    def test_index_renders_from_the_cache(self):
        calls = []
        with patch.object(web_panel, 'check_pc_status', _fake_fetcher('UNLOCKED', calls)), \
             patch.object(web_panel, 'get_current_user', _fake_fetcher('Kids', [])), \
             patch.object(web_panel, 'get_usage_limit', _fake_fetcher(120, [])), \
             patch.object(web_panel, 'get_time_remaining', _fake_fetcher('60', [])), \
             patch.object(web_panel, 'get_lock_times', _fake_fetcher(None, [])), \
             patch.object(web_panel, 'get_allowed_window', _fake_fetcher(None, []), create=True), \
             patch.object(web_panel, 'get_unlock_status', _fake_fetcher('INACTIVE', []), create=True):
            client = web_panel.app.test_client()
            first = client.get('/')
            second = client.get('/')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(calls), 1, 'second render hit the agent again')

    def test_state_changing_action_invalidates_the_cache(self):
        with patch.object(web_panel, 'check_pc_status', _fake_fetcher('UNLOCKED', [])), \
             patch.object(web_panel, 'get_current_user', _fake_fetcher('Kids', [])), \
             patch.object(web_panel, 'get_usage_limit', _fake_fetcher(120, [])), \
             patch.object(web_panel, 'get_time_remaining', _fake_fetcher('60', [])), \
             patch.object(web_panel, 'get_lock_times', _fake_fetcher(None, [])), \
             patch.object(web_panel, 'get_allowed_window', _fake_fetcher(None, []), create=True), \
             patch.object(web_panel, 'get_unlock_status', _fake_fetcher('INACTIVE', []), create=True), \
             patch.object(web_panel, 'send_command', return_value=(True, 'OK')):
            web_panel._pc_details(IP)
            self.assertIn(IP, web_panel.status_cache)
            response = web_panel.app.test_client().post(
                '/action', json={'ip': IP, 'action': 'lock'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertNotIn(IP, web_panel.status_cache)


if __name__ == '__main__':
    unittest.main()

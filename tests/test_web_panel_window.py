"""Tests for the allowed-window and temporary-unlock UI in the panel.

The window is off unless a parent sets one, so the page must say so instead of
showing an invented default, and the unlock button must send the right agent
command. Run with `python -m unittest discover -s tests` (or pytest).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import web_panel  # noqa: E402

IP = '192.0.2.20'


class ControlPageTests(unittest.TestCase):
    def setUp(self):
        web_panel.status_cache.clear()
        web_panel.discovered_pcs.clear()
        web_panel.discovered_pcs[IP] = {'hostname': 'PC', 'status': 'online'}

    def tearDown(self):
        web_panel.status_cache.clear()
        web_panel.discovered_pcs.clear()

    def _render(self, details):
        with patch.object(web_panel, '_pc_details', return_value=details):
            return web_panel.app.test_client().get(f'/control/{IP}')

    def test_no_window_is_reported_as_off_not_as_a_default(self):
        response = self._render({'status': 'UNLOCKED'})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Off (PC usable any time)', response.data)
        self.assertNotIn(b'07:00-22:00', response.data)

    def test_configured_window_is_shown_with_a_turn_off_button(self):
        response = self._render({'status': 'UNLOCKED', 'allowed_window': '07:00-21:30'})

        self.assertIn(b'07:00-21:30', response.data)
        self.assertIn('Turn off'.encode(), response.data)

    def test_active_unlock_is_shown_with_a_cancel_button(self):
        response = self._render({'status': 'UNLOCKED',
                                 'unlock_status': 'ACTIVE 24 minutes'})

        self.assertIn(b'ACTIVE 24 minutes', response.data)
        self.assertIn(b'Cancel', response.data)

    def test_inactive_unlock_shows_no_cancel_button(self):
        response = self._render({'status': 'UNLOCKED', 'unlock_status': 'INACTIVE'})

        self.assertNotIn(b'ACTIVE 24 minutes', response.data)


class ActionPayloadTests(unittest.TestCase):
    def setUp(self):
        web_panel.status_cache.clear()
        web_panel.discovered_pcs.clear()

    def _post(self, payload, response=(True, 'OK')):
        with patch.object(web_panel, 'send_command', return_value=response) as send:
            result = web_panel.app.test_client().post('/action', json=payload)
        return send, result

    def test_unlock_sends_the_minutes(self):
        send, result = self._post({'ip': IP, 'action': 'unlock', 'minutes': 45})

        send.assert_called_once_with(IP, 'UNLOCK:45')
        self.assertTrue(result.get_json()['success'])

    def test_cancel_unlock_sends_the_command(self):
        send, _ = self._post({'ip': IP, 'action': 'cancel_unlock'})

        send.assert_called_once_with(IP, 'CANCEL_UNLOCK')

    def test_set_allowed_window_sends_the_range(self):
        send, _ = self._post({'ip': IP, 'action': 'set_allowed_window',
                              'window': '07:00-21:30'})

        send.assert_called_once_with(IP, 'SET_ALLOWED_WINDOW:07:00-21:30')

    def test_clear_allowed_window_sends_the_command(self):
        send, _ = self._post({'ip': IP, 'action': 'clear_allowed_window'})

        send.assert_called_once_with(IP, 'CLEAR_ALLOWED_WINDOW')


class CacheFieldTests(unittest.TestCase):
    def setUp(self):
        web_panel.status_cache.clear()

    def tearDown(self):
        web_panel.status_cache.clear()

    def test_window_and_unlock_are_part_of_the_cached_fields(self):
        calls = []
        with patch.object(web_panel, 'check_pc_status', return_value='UNLOCKED'), \
             patch.object(web_panel, 'get_current_user', return_value='Kids'), \
             patch.object(web_panel, 'get_usage_limit', return_value=120), \
             patch.object(web_panel, 'get_time_remaining', return_value='60'), \
             patch.object(web_panel, 'get_lock_times', return_value=None), \
             patch.object(web_panel, 'get_allowed_window',
                          side_effect=lambda ip, port=9999: calls.append('window') or '07:00-22:00'), \
             patch.object(web_panel, 'get_unlock_status',
                          side_effect=lambda ip, port=9999: calls.append('unlock') or 'INACTIVE'):
            details = web_panel._pc_details(IP)
            web_panel._pc_details(IP)

        self.assertEqual(details['allowed_window'], '07:00-22:00')
        self.assertEqual(details['unlock_status'], 'INACTIVE')
        # One round trip each, even though the page rendered twice.
        self.assertEqual(calls, ['window', 'unlock'])


class WindowCommandParsingTests(unittest.TestCase):
    """The panel treats the agent's OFF answer as "no window", not as a value."""

    def _parse(self, answer):
        class FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def settimeout(self, *a):
                pass

            def connect(self, *a):
                pass

            def send(self, *a):
                pass

            def recv(self, *a):
                return answer.encode()

            def close(self):
                pass

        with patch.object(web_panel.socket, 'socket', FakeSocket):
            return web_panel.get_allowed_window(IP)

    def test_off_means_no_window(self):
        self.assertIsNone(self._parse('OFF'))

    def test_a_range_is_returned_verbatim(self):
        self.assertEqual(self._parse('07:00-21:30'), '07:00-21:30')


if __name__ == '__main__':
    unittest.main()

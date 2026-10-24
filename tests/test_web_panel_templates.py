"""The panel serves the templates it just generated.

Flask's root path is src/, so the templates must be written to src/templates.
Writing them to the current working directory made the panel serve whatever
stale copy happened to sit in src/templates whenever it was started from
another directory (systemd, a scheduler, a test run from the repo root).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import web_panel  # noqa: E402

IP = '192.0.2.30'


class TemplateLocationTests(unittest.TestCase):
    def setUp(self):
        web_panel.status_cache.clear()
        web_panel.discovered_pcs.clear()
        web_panel.discovered_pcs[IP] = {'hostname': 'PC', 'status': 'online'}

    def tearDown(self):
        web_panel.status_cache.clear()
        web_panel.discovered_pcs.clear()

    def test_templates_are_written_next_to_the_module(self):
        template_dir = Path(web_panel.__file__).resolve().parent / 'templates'

        self.assertTrue((template_dir / 'index.html').is_file())
        self.assertTrue((template_dir / 'control.html').is_file())

    def test_served_page_comes_from_the_generated_template(self):
        template_dir = Path(web_panel.__file__).resolve().parent / 'templates'
        on_disk = (template_dir / 'control.html').read_text(encoding='utf-8')

        self.assertEqual(on_disk, web_panel.CONTROL_TEMPLATE)

        with patch.object(web_panel, '_pc_details', return_value={'status': 'UNLOCKED'}):
            response = web_panel.app.test_client().get(f'/control/{IP}')

        self.assertEqual(response.status_code, 200)
        # A marker that exists only in the current template generation.
        self.assertIn(b'Current Settings', response.data)


if __name__ == '__main__':
    unittest.main()

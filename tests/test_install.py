import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from scripts import install


class ScheduledTaskPathTests(unittest.TestCase):
    def test_quotes_program_files_script_as_one_windows_argument(self):
        script_path = r"C:\Program Files\KidPCMonitor\src\pc_control.py"

        self.assertEqual(
            install.quote_windows_argument(script_path),
            f'"{script_path}"',
        )

    def test_escapes_single_quote_in_powershell_literal(self):
        self.assertEqual(
            install.quote_powershell_literal("C:\\Parent's App"),
            "'C:\\Parent''s App'",
        )

    def test_powershell_task_action_keeps_program_files_path_together(self):
        script_path = r"C:\Program Files\KidPCMonitor\src\pc_control.py"
        successful_run = CompletedProcess([], 0, stdout="ok", stderr="")

        with patch("builtins.input", return_value="y"), patch.object(
            install.subprocess,
            "run",
            side_effect=[successful_run, successful_run],
        ) as run:
            created = install.create_task_with_power_settings(script_path, "Kids")

        self.assertTrue(created)
        powershell_command = run.call_args_list[0].args[0][4]
        self.assertIn(
            "-Argument '\"C:\\Program Files\\KidPCMonitor\\src\\pc_control.py\"'",
            powershell_command,
        )


if __name__ == "__main__":
    unittest.main()

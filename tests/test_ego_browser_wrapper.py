import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


WRAPPER = Path(__file__).resolve().parents[1] / 'tools' / 'ego-browser'


class EgoBrowserWrapper(unittest.TestCase):
    def test_one_browser_command_records_its_action_once_for_all_page_screenshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_dir = root / 'evidence'
            real_ego = root / 'real-ego'
            real_ego.write_text(
                '#!/usr/bin/env python3\n'
                'import json, re, sys\n'
                'from pathlib import Path\n'
                'source = sys.stdin.read()\n'
                'marker = "@@jobsalvo-ego-shot@@"\n'
                'if marker not in source:\n'
                '    raise SystemExit(0)\n'
                'prefix = json.loads(re.search(r"const prefix=(.*);", source).group(1))\n'
                'workspaces = json.loads(re.search(r"const workspaces=(.*);", source).group(1))\n'
                'rows = []\n'
                'for number, page in enumerate(("profile", "education"), 1):\n'
                '    file = f"{prefix}-{number:02}.png"\n'
                '    Path(file).write_bytes(b"png")\n'
                '    rows.append({"ok": True, "workspace": workspaces[0], "page": page,\n'
                '                 "url": "https://jobs.test/" + page, "title": page.title(),\n'
                '                 "file": file, "screenshot_ms": 1})\n'
                'print(marker + json.dumps(rows))\n',
                encoding='utf-8',
            )
            real_ego.chmod(0o755)
            env = os.environ.copy()
            env.update({
                'JOBSALVO_EGO_REAL_BIN': str(real_ego),
                'JOBSALVO_EGO_EVIDENCE_DIRS': json.dumps([str(evidence_dir)]),
            })
            script = (
                'const task = await taskSpace(42); '
                'await task.page("profile").locator("#summary").fill("private resume text"); '
                'await task.page("profile").locator("#save").click();'
            )

            result = subprocess.run(
                [sys.executable, str(WRAPPER), 'nodejs', '-e', script],
                env=env, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            events = [json.loads(line) for line in (evidence_dir / 'events.jsonl').read_text().splitlines()]
            shots = [event for event in events if event.get('kind') == 'shot']
            self.assertEqual([event['page'] for event in shots], ['profile', 'education'])
            self.assertEqual(len({event['command_id'] for event in shots}), 1)
            self.assertEqual({event['action'] for event in shots}, {'填寫欄位、按下儲存'})
            self.assertNotIn('private resume text', (evidence_dir / 'events.jsonl').read_text())

    def _recording_ego(self, root):
        real_ego = root / 'real-ego'
        real_ego.write_text(
            '#!/usr/bin/env python3\n'
            'import json, sys\n'
            'from pathlib import Path\n'
            'source = sys.stdin.read()\n'
            'if "@@jobsalvo-ego-shot@@" in source:\n'
            '    print("@@jobsalvo-ego-shot@@[]")\n'
            'else:\n'
            f'    Path({str(root / "received.json")!r}).write_text(json.dumps([sys.argv[1:], source]))\n',
            encoding='utf-8',
        )
        real_ego.chmod(0o755)
        env = os.environ.copy()
        env.update({'JOBSALVO_EGO_REAL_BIN': str(real_ego),
                    'JOBSALVO_EGO_EVIDENCE_DIRS': json.dumps([str(root / 'evidence')])})
        return env

    def test_a_script_given_with_e_returns_while_the_agent_shell_keeps_stdin_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._recording_ego(root)
            proc = subprocess.Popen(
                [sys.executable, str(WRAPPER), 'nodejs', '-e', 'await taskSpace(42);'],
                env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                self.fail('ego-browser -e 等著讀一條不會關的標準輸入,指令沒有回來')
            finally:
                proc.stdin.close()
            self.assertEqual(proc.returncode, 0)
            argv, source = json.loads((root / 'received.json').read_text())
            self.assertEqual(argv, ['nodejs', '-e', 'await taskSpace(42);'])
            self.assertEqual(source, '')

    def test_a_script_given_with_e_after_another_flag_also_skips_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = subprocess.Popen(
                [sys.executable, str(WRAPPER), 'nodejs', '--quiet', '-e', 'await taskSpace(42);'],
                env=self._recording_ego(root), stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                self.fail('ego-browser nodejs --quiet -e 等著讀一條不會關的標準輸入')
            finally:
                proc.stdin.close()
            self.assertEqual(json.loads((root / 'received.json').read_text())[1], '')

    def test_a_heredoc_script_still_reaches_ego(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = subprocess.run(
                [sys.executable, str(WRAPPER), 'nodejs'], input='await taskSpace(42);\n',
                env=self._recording_ego(root), capture_output=True, text=True, check=False, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root / 'received.json').read_text()),
                             [['nodejs'], 'await taskSpace(42);\n'])

    def test_unwritable_evidence_directory_is_recorded_without_changing_ego_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocker = root / 'file'
            blocker.write_text('not a directory', encoding='utf-8')
            bad_directory = blocker / 'round'
            good_directory = root / 'evidence'
            real_ego = root / 'real-ego'
            real_ego.write_text(
                '#!/usr/bin/env python3\n'
                'import sys\n'
                'source = sys.stdin.read()\n'
                'if "@@jobsalvo-ego-shot@@" in source:\n'
                '    print("@@jobsalvo-ego-shot@@[]")\n',
                encoding='utf-8',
            )
            real_ego.chmod(0o755)
            env = os.environ.copy()
            env.update({
                'JOBSALVO_EGO_REAL_BIN': str(real_ego),
                'JOBSALVO_EGO_EVIDENCE_DIRS': json.dumps([str(bad_directory), str(good_directory)]),
            })

            result = subprocess.run(
                [sys.executable, str(WRAPPER), 'nodejs', '-e', 'await taskSpace(42);'],
                env=env, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            events = [json.loads(line) for line in (good_directory / 'events.jsonl').read_text().splitlines()]
            failure = next((event for event in events if event.get('what') == 'ego_screenshot_failed'), None)
            self.assertIsNotNone(failure, events)
            self.assertEqual(failure['step'], 'create_evidence_directory')
            self.assertIn(str(bad_directory), failure['reason'])

            env['JOBSALVO_EGO_EVIDENCE_DIRS'] = json.dumps([str(bad_directory)])
            no_writable_directory = subprocess.run(
                [sys.executable, str(WRAPPER), 'nodejs', '-e', 'await taskSpace(42);'],
                env=env, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=60,
            )
            self.assertEqual(no_writable_directory.returncode, 0, no_writable_directory.stderr)
            self.assertIn('證據記不下來', no_writable_directory.stderr)


    def test_a_real_command_pointing_back_at_the_wrapper_says_why_it_stops(self):
        # 答案來源:.audit/exam1-diagnosis.md 第一節;轉接器不出聲回 127,agent 看不懂第一個指令為什麼失敗。
        env = os.environ.copy()
        env['JOBSALVO_EGO_REAL_BIN'] = str(WRAPPER)
        result = subprocess.run([sys.executable, str(WRAPPER), 'skill'], env=env, capture_output=True,
                                text=True, check=False, stdin=subprocess.DEVNULL, timeout=60)

        self.assertEqual(127, result.returncode)
        self.assertIn('JOBSALVO_EGO_REAL_BIN 指向轉接器自己', result.stderr)

if __name__ == '__main__':
    unittest.main()

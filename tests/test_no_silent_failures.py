# -*- coding: utf-8 -*-
"""不准默默失敗(#301):抓例外的地方要嘛只抓預期的那種、要嘛讓使用者看得到、要嘛寫出為什麼可以不管。

ruff 負責找出「抓所有例外 / 吞掉例外」的地方(pyproject 的 BLE001、SIM105、S110、S112、E722);
真的無害而保留的,要在同一行寫 `# noqa: <規則> — <理由>`。這裡檢查:
  · 這幾條規則有開著(關掉了 ruff 就什麼都不列);
  · 每個蓋掉這幾條規則的 noqa 都寫了理由;
  · 沒有人用 contextlib.suppress(Exception) 繞過(BLE001 不看 suppress 的參數);
  · 覆蓋率排除(pragma: no cover / no branch)也要寫為什麼不用測。
"""
import glob
import json
import os
import re
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RULES = ('BLE001', 'SIM105', 'S110', 'S112', 'E722')
NOQA = re.compile(r'#\s*noqa:\s*((?:[A-Z]+[0-9]+)(?:\s*,\s*[A-Z]+[0-9]+)*)(.*)$')
PRAGMA = re.compile(r'#\s*pragma:\s*no\s+(?:cover|branch)\b(.*)$')
BLIND_SUPPRESS = re.compile(r'suppress\(\s*(?:Exception|BaseException)\s*[,)]')


def _python_files():
    for folder in ('tools', 'tests'):
        yield from sorted(glob.glob(os.path.join(ROOT, folder, '**', '*.py'), recursive=True))


def _lines():
    for path in _python_files():
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding='utf-8') as fh:
            for n, line in enumerate(fh, 1):
                yield rel, n, line.rstrip('\n')


def _reason(rest):
    """noqa / pragma 後面的字去掉分隔符號後剩下的理由。"""
    return rest.strip().lstrip('—-–:: ').strip()


class NoSilentFailures(unittest.TestCase):
    def test_rules_are_on(self):
        with open(os.path.join(ROOT, 'pyproject.toml'), 'rb') as fh:
            select = tomllib.load(fh)['tool']['ruff']['lint']['select']
        self.assertEqual([r for r in RULES if r not in select], [],
                         'pyproject.toml 的 ruff 沒開這幾條,吞掉例外的地方就沒人列出來')

    def test_every_noqa_for_these_rules_says_why(self):
        bad = []
        for rel, n, line in _lines():
            m = NOQA.search(line)
            if not m:
                continue
            codes = {c.strip() for c in m.group(1).split(',')}
            if codes & set(RULES) and not _reason(m.group(2)):
                bad.append(f'{rel}:{n}: {line.strip()}')
        self.assertEqual(bad, [], '這幾處蓋掉「不准默默失敗」的規則卻沒寫理由(在同一行 — 後面寫為什麼可以不管):\n'
                         + '\n'.join(bad))

    def test_no_blind_suppress(self):
        bad = [f'{rel}:{n}: {line.strip()}' for rel, n, line in _lines()
               if BLIND_SUPPRESS.search(line) and rel != os.path.relpath(__file__, ROOT)]
        self.assertEqual(bad, [], 'contextlib.suppress(Exception) 等於 except Exception: pass,ruff 不會列:\n'
                         + '\n'.join(bad))

    def test_every_coverage_exclusion_says_why(self):
        bad = [f'{rel}:{n}: {line.strip()}' for rel, n, line in _lines()
               if (m := PRAGMA.search(line)) and not _reason(m.group(1))]
        self.assertEqual(bad, [], '這幾處不算覆蓋率卻沒寫為什麼不用測:\n' + '\n'.join(bad))

    def test_reason_parser(self):
        # 理由要是真的字,不是只有分隔符號
        self.assertEqual(_reason(' — 盡力清掉暫存檔'), '盡力清掉暫存檔')
        self.assertEqual(_reason(' — '), '')
        self.assertEqual(_reason(''), '')
        m = NOQA.search('except Exception:  # noqa: BLE001, S110 — 盡力清暫存')
        self.assertEqual((m.group(1), _reason(m.group(2))), ('BLE001, S110', '盡力清暫存'))
        self.assertIsNone(PRAGMA.search('x = 1  # 普通註解'))


class FailuresReachTheBoard(unittest.TestCase):
    """碰到他資料的地方出錯,要寫進看板最上面的「📣 回報」(不是只印在伺服器的終端機,他看不到)。"""

    def setUp(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, 'tests'))
        import _env  # noqa: F401  測試跑在暫存資料夾
        import board_doc as bd
        import board_server as bs
        self.bd, self.bs = bd, bs
        self.build = bs.trigger_build
        self.dir = self.enterContext(tempfile.TemporaryDirectory(prefix='loud-'))
        self.path = os.path.join(self.dir, 'board.html')
        self.url = 'https://ex.test/job/1'
        _env.make_board(self.path, jobs=[{'id': self.url, 'target': 'x'}])
        patch = mock.patch.multiple(bs, STATE=self.path, trigger_build=lambda: None)
        patch.start()
        self.addCleanup(patch.stop)

    def reports(self):
        import agent_report
        with open(self.path, encoding='utf-8') as fh:
            fb = json.loads(self.bd.parse(fh.read())['fb'])
        return [it['msg'] for it in agent_report.open_items(fb)]

    def test_folder_history_not_scheduled_is_reported(self):
        with mock.patch('folder_history.note_saved', side_effect=OSError('磁碟滿了')):
            self.assertEqual(self.bs.write_fb({self.url: {'s': 'like'}}), [])
        self.assertTrue(any('版本紀錄' in m and '磁碟滿了' in m for m in self.reports()), self.reports())

    def test_sent_version_not_recorded_is_reported(self):
        with mock.patch('ship.record_sent', side_effect=OSError('讀不到履歷檔')):
            rejected = []
            self.bs.write_fb({}, events=[{'u': self.url, 'ev': 'sent_manual'}], rejected=rejected)
        self.assertEqual(rejected, [])
        self.assertTrue(any('寄出的是哪一份' in m and '讀不到履歷檔' in m for m in self.reports()), self.reports())

    def test_marks_journal_that_cannot_be_written_is_reported(self):
        # 流水帳是標記寫壞時唯一救得回來的紀錄:寫不進去照樣存檔,但看板上要看得到
        os.makedirs(self.bd.journal_path(self.path))          # 同名資料夾擋住,開不了檔
        self.bd.set_fb(lambda fb: fb.__setitem__(self.url, {'s': 'like'}), live=self.path, by='test')
        with open(self.path, encoding='utf-8') as fh:
            self.assertEqual(json.loads(self.bd.parse(fh.read())['fb'])[self.url], {'s': 'like'})
        self.assertTrue(any('流水帳' in m for m in self.reports()), self.reports())

    def test_background_build_that_cannot_start_is_reported(self):
        import time
        with mock.patch.object(self.bd, 'LIVE', self.path), \
             mock.patch('subprocess.run', side_effect=OSError('找不到 python')):
            self.build()
            for _ in range(100):
                with self.bs._BUILD_LOCK:
                    if not self.bs._build_state['running']:
                        break
                time.sleep(0.05)
        self.assertTrue(any('可投遞夾' in m and '找不到 python' in m for m in self.reports()), self.reports())


class BoardServerSaysWhatWentWrong(unittest.TestCase):
    """看板伺服器收到壞掉的請求、組不出頁面時,照實說,不拿空的或舊的頂上去。"""

    def setUp(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, 'tests'))
        import test_board as tb
        self.tb = tb
        self.server = type('S', (tb.HttpBase,), {'runTest': lambda s: None})()
        self.server.setUp()
        self.addCleanup(self.server.tearDown)

    def test_run_request_with_a_broken_body_is_refused(self):
        # 以前讀不懂就當成沒帶參數照跑:該只跑一張的變成全部都跑
        import board_server as bs
        with mock.patch.object(bs, 'start_run', side_effect=AssertionError('讀不懂的請求不該發動')):
            code, raw, _ = self.server.req('/api/run/replies', ['不是', '物件'])
        self.assertEqual(code, 400)
        self.assertIn('格式不對', raw.decode('utf-8'))

    def test_prompt_preview_says_it_failed_instead_of_no_matching_card(self):
        # 以前組例子出錯被當成「現在沒有符合這個階段的卡」
        import board_server as bs
        with mock.patch('apply_run.preview', return_value='程式給的說明'), \
             mock.patch('apply_run.preview_meta', side_effect=RuntimeError('看板讀不懂')), \
             mock.patch.dict(bs._PV_CACHE, clear=True):
            code, raw, _ = self.server.req('/api/prompt?kind=apply&stage=fill')
        got = json.loads(raw)
        self.assertEqual(code, 200)
        self.assertIn('看板讀不懂', got['prompt'])
        self.assertNotIn('沒有符合', got['note'])

    def test_page_that_cannot_be_assembled_is_reported(self):
        import agent_report
        import board_server as bs
        with mock.patch.object(bs, 'page_cfg', side_effect=KeyError('read_lang')):
            code, _, _ = self.server.req('/')
        self.assertEqual(code, 200)                     # 頁面照樣打得開(送原檔)
        msgs = [it['msg'] for it in agent_report.open_items(self.tb.read_fb(self.server.path))]
        self.assertTrue(any('看板頁面' in m and 'read_lang' in m for m in msgs), msgs)


class RunLogSaysWhatCouldNotBeReported(unittest.TestCase):
    """跑準備區的回報寫不進看板時,至少印進這一輪的紀錄(看板上「看紀錄」看得到),不是整句不見。"""

    def test_a_report_that_cannot_reach_the_board_goes_to_the_run_log(self):
        """跑準備區、找缺、照網址加缺各一份。"""
        import contextlib
        import io
        import sys
        sys.path.insert(0, os.path.join(ROOT, 'tests'))
        import _env  # noqa: F401  測試跑在暫存資料夾
        import add_job
        import cut_tailor
        import research
        for module, msg, need in ((cut_tailor, '準備履歷 agent 沒完成', '看紀錄再重跑'),
                                  (research, '找缺 agent 沒寫出候選', '看紀錄再重跑'),
                                  (add_job, '2 個網址確定已下架', '確認網址對不對')):
            with self.subTest(module.__name__):
                out = io.StringIO()
                with mock.patch('agent_report.report', side_effect=OSError('看板檔被鎖住')), contextlib.redirect_stdout(out):
                    module._report(msg, need, '/nowhere/board.html')
                self.assertIn(msg, out.getvalue())
                self.assertIn('看板檔被鎖住', out.getvalue())

    def test_progress_that_cannot_be_written_goes_to_the_run_log(self):
        # 進度檔寫不進去,看板上的進度就停在上一格:至少這一輪的紀錄要講
        import contextlib
        import io
        import jobrun
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            jobrun.write(os.path.join(ROOT, 'no-such-folder', 'status.json'), {'phase': 'agent'})
        self.assertIn('進度', err.getvalue())
        self.assertIn('status.json', err.getvalue())


if __name__ == '__main__':
    unittest.main()

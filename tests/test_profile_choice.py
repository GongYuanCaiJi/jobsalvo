"""投遞前的方式判讀必須以程式讀到的申請頁為準。"""
import os
import sys
import unittest
import json
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402
import gate  # noqa: E402
import apply_run  # noqa: E402
import fake_door  # noqa: E402


class ProfileChoice(unittest.TestCase):
    def setUp(self):
        self.page = {'title': '申請表', 'lines': ['選擇平台履歷'],
                     'fields': [{'label': 'Platform resume', 'type': 'select-one', 'value': 'fixed'}]}
        self.truth = gate.Truth(page=self.page, tab_id='1:p1')
        self.honest = {'tab_id': '1:p1'}

    def test_the_program_reads_the_delivery_method_off_the_page(self):
        from gate_profile_choice import method_of
        self.assertEqual(method_of(self.page), 'platform_profile')
        self.assertEqual(method_of(dict(self.page, fields=self.page['fields'] + [{'type': 'file'}])), 'direct_upload')
        self.assertEqual(method_of({'fields': [{'label': 'Name', 'type': 'text'}]}), 'no_profile')
        self.assertEqual(method_of({'fields': [], 'profileLinks': ['https://p.example/r?vno=1']}), 'platform_profile')

    def test_tab_outside_the_card_space_is_blocked(self):
        verdict = gate.inspect('profile_choice', dict(self.honest, tab_id='2:p9'), self.truth)
        self.assertTrue(verdict.problems)
        self.assertFalse(gate.inspect('profile_choice', self.honest, self.truth).problems)

    def test_delivery_preflight_allows_agent_handoff_before_sync(self):
        _env.use_home(self)
        board = _env.make_board(os.path.join(self.tmp, 'board.html'))
        door = fake_door.FakeDoor(agent_id='fake', page=self.page)
        chosen = []

        def agent(_prompt, _log, _home, _board, **kwargs):
            chosen.append(kwargs.get('agent_id'))
            with open(kwargs['output_last_message'], 'w', encoding='utf-8') as out:
                json.dump(self.honest, out)
            return SimpleNamespace(ok=True, agent_id='backup')

        with fake_door.installed(door), mock.patch.object(apply_run, '_run_agent', side_effect=agent):
            method, _prepared, problem = apply_run._choose_delivery_before_fill(
                'https://platform.example/job/one', board, door, {'tab_id': '1:p1'})
        self.assertEqual((method, problem), ('platform_profile', ''))
        self.assertEqual(chosen, [None])

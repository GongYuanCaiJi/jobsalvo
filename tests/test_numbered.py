#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""段落編號(#465):程式交給 agent 的材料邊印邊編號,agent 只回整數清單;收不收只看 pick 一處。"""
import copy
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
from numbered import ROW, Accept, Cited, Miscited, Numbered, pick  # noqa: E402

J1, J2 = SimpleNamespace(key='J1'), SimpleNamespace(key='J2')


def two_jobs():
    numbered = Numbered()
    shared = numbered.add('他喜歡資料管線\n\n不想當主管\n', 'note')
    first = numbered.add('資料工程師\n負責排程', 'jd', owner='J1')
    second = numbered.add('後端工程師', 'jd', owner='J2')
    flag = numbered.add('刊登超過 90 天', 'flag', owner='J3')
    return numbered, (shared, first, second, flag)


class Printing(unittest.TestCase):
    def test_blocks_number_on_from_each_other_one_line_per_part(self):
        _, blocks = two_jobs()
        self.assertEqual(blocks, ('1. 他喜歡資料管線\n2. 不想當主管', '3. 資料工程師\n4. 負責排程',
                                  '5. 後端工程師', '6. 刊登超過 90 天'))

    def test_a_list_is_one_part_per_item_as_is_and_empty_text_makes_no_part(self):
        numbered = Numbered()
        self.assertEqual(numbered.add(['  作品  ', '', '舊影片'], 'line'), '1.   作品  \n2. 舊影片')
        self.assertEqual(numbered.add('', 'jd', owner='J1'), '')
        self.assertEqual(numbered.add('\n \n', 'jd', owner='J1'), '')
        self.assertEqual(numbered.add([], 'pnote'), '')
        self.assertEqual([p.n for p in numbered.parts], [1, 2])

    def test_held_names_owner_and_kind_so_a_flag_alone_is_not_a_jd(self):
        numbered, _ = two_jobs()
        held = numbered.held()
        self.assertEqual(held, {(None, 'note'), ('J1', 'jd'), ('J2', 'jd'), ('J3', 'flag')})
        self.assertNotIn(('J3', 'jd'), held)

    def test_every_delivery_has_its_own_id_and_dump_load_keeps_it(self):
        numbered, _ = two_jobs()
        self.assertNotEqual(numbered.material_id, Numbered().material_id)
        back = Numbered.load(numbered.dump())
        self.assertEqual(back.material_id, numbered.material_id)
        self.assertEqual(pick(back, [3, 4], Accept(owner=ROW), truth=J1),
                         pick(numbered, [3, 4], Accept(owner=ROW), truth=J1))


class Picking(unittest.TestCase):
    def setUp(self):
        self.numbered, _ = two_jobs()

    def refused(self, ids, accept=Accept(), truth=J1, item=None):
        with self.assertRaises(Miscited) as caught:
            pick(self.numbered, ids, accept, truth=truth, item=item)
        return str(caught.exception)

    def test_cited_numbers_become_the_program_text(self):
        got = pick(self.numbered, [3, 4], Accept(frozenset({'jd'}), ROW), truth=J1)
        self.assertEqual(got, '資料工程師\n負責排程')
        self.assertEqual([p.n for p in got.parts], [3, 4])
        self.assertEqual(copy.deepcopy(got).parts, got.parts)

    def test_an_empty_list_is_nothing_cited(self):
        got = pick(self.numbered, [], Accept(frozenset({'jd'}), ROW), truth=J1)
        self.assertEqual((got, got.parts), ('', ()))
        self.assertIsInstance(got, Cited)

    def test_anything_but_a_list_of_whole_numbers_is_refused(self):
        for ids in ('資料工程師', ['資料工程師'], [True], ['3'], 3, None, [3.0]):
            with self.subTest(ids=ids):
                self.assertIn('段落編號無效', self.refused(ids))

    def test_a_repeated_or_made_up_number_is_refused_and_named(self):
        said = self.refused([3, 3])
        self.assertIn('3', said)
        self.assertIn('重複', said)
        said = self.refused([9])
        self.assertIn('9', said)
        self.assertIn('1-6', said)

    def test_another_kind_is_refused_with_its_text(self):
        said = self.refused([1], Accept(frozenset({'jd'})))
        self.assertIn('他喜歡資料管線', said)
        self.assertIn('偏好筆記', said)
        self.assertIn('JD 原文', said)

    def test_another_rows_number_is_refused_but_shared_material_counts_for_every_row(self):
        said = self.refused([5], Accept(owner=ROW))
        self.assertIn('後端工程師', said)
        self.assertIn('J2', said)
        self.assertEqual(pick(self.numbered, [1, 3], Accept(owner=ROW), truth=J1), '他喜歡資料管線\n資料工程師')
        self.assertEqual(pick(self.numbered, [2, 5], Accept(owner=ROW), truth=J2), '不想當主管\n後端工程師')

    def test_a_cell_that_takes_no_kind_says_so(self):
        self.assertIn('這一格不收任何段落', self.refused([1], Accept(frozenset())))

    def test_a_number_of_another_kind_and_another_row_names_both(self):
        said = self.refused([5], Accept(frozenset({'note'}), ROW))
        self.assertIn('JD 原文', said)
        self.assertIn('J2', said)

    def test_a_list_cell_takes_its_kinds_from_the_item_being_checked(self):
        by_basis = Accept(lambda item: frozenset({'jd'}) if item.get('basis') == 'JD 原文' else frozenset({'note'}), ROW)
        self.assertEqual(pick(self.numbered, [3], by_basis, truth=J1, item={'basis': 'JD 原文'}), '資料工程師')
        self.assertEqual(pick(self.numbered, [1], by_basis, truth=J1, item={'basis': '偏好筆記'}), '他喜歡資料管線')
        self.assertIn('資料工程師', self.refused([3], by_basis, item={'basis': '偏好筆記'}))

    def test_a_value_cell_takes_the_value_only_whole_inside_one_cited_paragraph(self):
        value = Accept(frozenset({'jd'}), ROW, value='text')
        got = pick(self.numbered, [3, 4], value, truth=J1, item={'text': '工程師'})
        self.assertEqual((got, [p.n for p in got.parts]), ('工程師', [3, 4]))
        self.assertEqual(pick(self.numbered, [], value, truth=J1, item={'text': ''}), '', '查不到:值和段落一起空')
        self.assertIn('沒有一字不差', self.refused([3, 4], value, item={'text': '工程師\n負責'}))   # 兩段接起來才有
        self.assertIn('沒有一字不差', self.refused([3], value, item={'text': '資料 工程師'}))
        self.assertIn('沒寫 text', self.refused([3], value, item={'text': ''}))
        self.assertIn('一起寫或一起空', self.refused([], value, item={'text': '資料工程師'}))
        self.assertIn('字串', self.refused([3], value, item={}))

    def test_a_refusal_is_a_value_error(self):
        self.assertTrue(issubclass(Miscited, ValueError))


if __name__ == '__main__':
    unittest.main()

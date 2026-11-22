#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""填表任務裡程式依頁面下的判斷(docs/research/page-decisions.md):每一列一個從任務入口跑的劇本。

ADR-0011:程式碰頁面只准讀、比、核對 agent 引的字一字不差出現。答案來源寫在每個測試裡,不取自程式輸出。
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _env  # noqa: E402,F401
import board_doc as bd  # noqa: E402
import chrome_door  # noqa: E402
import form_plan as fp  # noqa: E402
from website_testbench import WebsiteTestBench  # noqa: E402

TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
U = 'https://jobs.example.test/acme/1'
OTHER = 'https://jobs.example.test/acme/2'
DOM_VALUES = """() => Object.fromEntries([...document.querySelectorAll('input,select,textarea')].map(e =>
    [e.name, ['checkbox', 'radio'].includes(e.type) ? e.checked : e.value]))"""


class FormPlanFillsTheApplicationPage(unittest.TestCase):
    """入口:agent 打開申請表後跑 `form_plan.py --url URL --tab 工作區:Page --fill`(或 --set);
    答案是測試台在那一刻讀到的頁面 DOM 值。"""

    def _form_plan(self, html, bank, *args, read=DOM_VALUES):
        d = self.enterContext(tempfile.TemporaryDirectory(prefix='fill-form-plan-'))
        bench = self.enterContext(WebsiteTestBench(pages={'/apply': html}))
        page_id = bench.open_page('/apply')
        door = bench.browser.door
        fb = {U: {'app': 'ship', 'apply': {'workspace': {'id': door.workspace['id'], 'name': 'website-testbench'}}},
              '__ans__': list(bank)}
        board = _env.make_board(os.path.join(d, 'board.html'), fb, jobs=[{'id': U}])
        tab = f"{door.workspace['id']}:{bench._page_labels[page_id]}"
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['form_plan.py', '--url', U, '--tab', tab, *args]), \
             mock.patch.object(bd, 'target', return_value=board), \
             mock.patch.object(chrome_door, 'EgoDoor', return_value=door), \
             contextlib.redirect_stdout(out):
            fp.main()
        return out.getvalue(), bench.run_agent_script(page_id, read)

    def test_a_menu_option_is_chosen_only_when_its_text_is_the_answer_character_for_character(self):
        """答案庫是「No」,選單只有「None of the above」跟「No, I do not」:沒有一字不差的選項,程式不選,
        交給 agent(舊程式比小寫、互相包含,選到「None of the above」)。"""
        html = ('<!doctype html><title>Apply</title><label>Do you need sponsorship?<select name="sp">'
                '<option value=""></option><option>None of the above</option><option>No, I do not</option>'
                '</select></label>')
        bank = [{'k': 'sp', 'q': 'Do you need sponsorship?', 'v': 'No'}]
        printed, dom = self._form_plan(html, bank, '--fill')
        self.assertEqual(dom['sp'], '')
        self.assertIn('程式填不上「Do you need sponsorship?」', printed)

    def test_a_menu_option_that_is_the_answer_character_for_character_is_chosen(self):
        html = ('<!doctype html><title>Apply</title><label>Do you need sponsorship?<select name="sp">'
                '<option value=""></option><option>None of the above</option><option>No</option>'
                '</select></label>')
        bank = [{'k': 'sp', 'q': 'Do you need sponsorship?', 'v': 'No'}]
        _printed, dom = self._form_plan(html, bank, '--fill')
        self.assertEqual(dom['sp'], 'No')


    COMBO = ('<!doctype html><title>Apply</title><label>Country<input role="combobox" name="country"'
             ' aria-label="Country" autocomplete="off"></label><div id="list"></div><script>'
             'const box = document.querySelector("input"), list = document.querySelector("#list");'
             'box.addEventListener("input", () => { list.innerHTML = "";'
             '  for (const name of %s) { if (!name.startsWith(box.value)) continue;'
             '    const o = document.createElement("div"); o.setAttribute("role", "option"); o.textContent = name;'
             '    o.addEventListener("click", () => { box.value = name; list.innerHTML = ""; }); list.append(o); } });'
             '</script>')

    def test_a_suggestion_is_clicked_only_when_its_text_is_the_answer_character_for_character(self):
        """答案庫是「Taiwan」,建議清單只有「Taiwan, Province of China」:程式不點開頭一樣的那一項,
        回填不上交給 agent(舊程式點開頭一樣的)。"""
        html = self.COMBO % json.dumps(['Taiwan, Province of China', 'Thailand'])
        bank = [{'k': 'c', 'q': 'Country', 'v': 'Taiwan'}]
        printed, dom = self._form_plan(html, bank, '--fill')
        self.assertNotEqual(dom['country'], 'Taiwan, Province of China')
        self.assertIn('程式填不上「Country」', printed)

    def test_a_suggestion_that_is_the_answer_character_for_character_is_clicked(self):
        html = self.COMBO % json.dumps(['Taiwan, Province of China', 'Taiwan'])
        bank = [{'k': 'c', 'q': 'Country', 'v': 'Taiwan'}]
        printed, dom = self._form_plan(html, bank, '--fill')
        self.assertEqual(dom['country'], 'Taiwan')
        self.assertNotIn('程式填不上', printed)

    def test_a_custom_menu_choice_is_read_as_shown_not_with_its_spaces_merged(self):
        """自製下拉選單:瀏覽器給這一格的值是空的,選好的字顯示在同一個框裡,中間是兩個空白(頁上照原樣排)。
        程式照讀到的字列成頁面已有值(舊程式把連續空白併成一個,跟答案庫的字一樣了)。答案是測試台讀到的框裡的字。"""
        html = ('<!doctype html><title>Apply</title><div><div role="combobox" aria-label="City" tabindex="0"></div>'
                '<span id="shown" style="white-space:pre">Taipei  City</span></div>')
        printed, shown = self._form_plan(html, [{'k': 'city', 'q': 'City', 'v': 'Taipei City'}], '--fill',
                                         read="() => document.querySelector('#shown').textContent")
        self.assertIn(f'- 頁面已有值 | 題目「City」: {shown}\n', printed)

    def test_question_names_and_a_rich_box_value_are_read_with_their_spaces(self):
        """瀏覽器給的格子名稱、選項組名稱前後有空白,富文字框裡的字前後也有空白:程式照讀到的字當題目與頁面已有值;
        答案庫的「City」跟「 City 」不是一字不差的同一題,程式不填(舊程式先去掉前後空白,配到了就填 Taipei)。
        答案是測試台在那一刻讀到的 aria-label 與框裡的字。"""
        html = ('<!doctype html><title>Apply</title><input name="city" aria-label=" City ">'
                '<div id="size" role="radiogroup" aria-label=" Size "><input type="radio" name="size" aria-label="L"></div>'
                '<div id="why" contenteditable="true" aria-label="Why" style="white-space:pre-wrap">  hello  </div>')
        bank = [{'k': 'city', 'q': 'City', 'v': 'Taipei'}, {'k': 'why', 'q': 'Why', 'v': 'hi'}]
        printed, page = self._form_plan(html, bank, '--fill', read="""() => ({
            city: document.querySelector('[name=city]').value,
            label: document.querySelector('[name=city]').getAttribute('aria-label'),
            group: document.querySelector('#size').getAttribute('aria-label'),
            why: document.querySelector('#why').textContent})""")
        self.assertEqual(page['city'], '', printed)
        self.assertIn(f'題目「{page["label"]}」', printed)
        self.assertIn(f'題目「{page["group"]}」', printed)
        self.assertIn(f'- 頁面已有值 | 題目「Why」: {page["why"]}\n', printed)

    UNNAMED = ('<!doctype html><title>Apply</title><p>What is your nationality?</p><input name="nat">'
               '<label>Full name<input name="name"></label>')
    NATIONALITY = [{'k': 'nat', 'q': 'What is your nationality?', 'v': 'Taiwan'}]

    def test_a_box_with_no_name_is_not_given_the_text_before_it_as_its_question(self):
        """瀏覽器沒給這一格名稱,前面那行字是不是它的題目由 agent 判斷;程式不照閱讀順序猜、不自己填,
        把格子編號與前面的字列給 agent(舊程式把前一行當題目,填了 Taiwan)。"""
        printed, dom = self._form_plan(self.UNNAMED, self.NATIONALITY, '--fill')
        self.assertEqual(dom['nat'], '')
        self.assertIn('#1', printed)
        self.assertIn('What is your nationality?', printed)

    def test_the_agent_names_the_question_of_a_box_and_the_program_fills_the_recorded_answer(self):
        printed, dom = self._form_plan(self.UNNAMED, self.NATIONALITY,
                                       '--set', json.dumps([{'id': '#1', 'q': 'What is your nationality?'}]))
        self.assertEqual(dom['nat'], 'Taiwan', printed)

    def test_a_question_the_agent_names_that_is_not_on_the_page_is_not_used(self):
        printed, dom = self._form_plan(self.UNNAMED, self.NATIONALITY,
                                       '--set', json.dumps([{'id': '#1', 'q': 'Your nationality'}]))
        self.assertEqual(dom['nat'], '')
        self.assertIn('頁上沒有一字不差的「Your nationality」', printed)

    MOTIVE = ('<!doctype html><title>Apply</title>'
              '<label>Why do you want to join 範例科技?<textarea name="why"></textarea></label>')
    MOTIVE_BANK = [{'k': 'why', 'q': 'Why do you want to join 範例科技?', 'v': 'I like the team.',
                    'zh': '我喜歡這個團隊。'}]

    def test_the_program_does_not_choose_the_answer_language_from_the_question(self):
        """英文表單的題目裡有中文公司名:填中文還是英文那一份由 agent 判斷,程式不看題目有沒有中文字
        (舊程式看到中文字就填了中文那一份)。兩種都列給 agent。"""
        printed, dom = self._form_plan(self.MOTIVE, self.MOTIVE_BANK, '--fill')
        self.assertEqual(dom['why'], '')
        self.assertIn('I like the team.', printed)
        self.assertIn('我喜歡這個團隊。', printed)

    def test_the_agent_says_the_form_language_and_the_program_fills_that_version(self):
        _printed, dom = self._form_plan(self.MOTIVE, self.MOTIVE_BANK, '--fill', '--lang', 'en')
        self.assertEqual(dom['why'], 'I like the team.')

    def test_a_two_language_answer_gives_the_half_in_the_language_the_agent_said(self):
        html = '<!doctype html><title>Apply</title><label>從哪裡得知?<input name="src"></label>'
        bank = [{'k': 'src', 'q': '從哪裡得知?', 'v': '公司徵才頁 / Company career page'}]
        _printed, dom = self._form_plan(html, bank, '--fill', '--lang', 'en')
        self.assertEqual(dom['src'], 'Company career page')

    def test_a_long_box_name_is_compared_as_read_not_cut_at_200_characters(self):
        """瀏覽器給的名稱 250 字,答案庫記的是同一句 250 字:一字不差,程式填(舊程式把名稱截到 200 字,配不到)。"""
        question = 'Q' + 'x' * 248 + '?'
        html = f'<!doctype html><title>Apply</title><label>{question}<input name="long"></label>'
        _printed, dom = self._form_plan(html, [{'k': 'l', 'q': question, 'v': 'Long answer'}], '--fill')
        self.assertEqual(dom['long'], 'Long answer')

    def test_a_locked_box_is_listed_and_left_alone(self):
        """鎖住(disabled)的空白格照列給 agent、標停用,程式不去填(舊程式讀頁時整格丟掉,agent 看不到它)。"""
        html = '<!doctype html><title>Apply</title><label>Email<input name="email" disabled></label>'
        printed, dom = self._form_plan(html, [{'k': 'e', 'q': 'Email', 'v': 'alex@example.com'}], '--fill')
        self.assertEqual(dom['email'], '')
        self.assertIn('題目「Email」', printed)
        self.assertIn('停用', printed)

    def test_a_box_right_after_another_box_has_no_text_before_it(self):
        """國碼選單後面緊接著沒有名稱的號碼格:兩格之間沒有字,號碼格「前面的字」是空的;
        程式不把上一格前面的字沿用過來(舊程式沿用了「Mobile number」)。"""
        html = ('<!doctype html><title>Apply</title><p>Mobile number</p><label>Country code<select name="cc">'
                '<option>+00</option></select></label><input name="num">')
        printed, _dom = self._form_plan(html, [], '--fill')
        line = next(row for row in printed.splitlines() if '格 #2' in row)
        self.assertNotIn('Mobile number', line)



if __name__ == '__main__':
    unittest.main()

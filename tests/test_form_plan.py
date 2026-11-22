#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""程式先配好答案(form_plan):以前同一題記過的答案、答案庫的題目 → 清單;配不到的才交給 agent。"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
import form_plan as fp  # noqa: E402

U, OTHER = 'https://jobs.example.test/a/1', 'https://jobs.example.test/b/2'


def board():
    return {
        '__ans__': [{'k': 'nat', 'q': '你的國籍', 'v': 'Taiwan', 'zh': '台灣'},
                    {'k': 'src', 'q': '從哪裡得知', 'v': '公司徵才頁 / Company career page'},
                    {'k': 'pending', 'q': 'Salary expectation', 'v': '', 'inf': 1}],
        OTHER: {'form': {'f': [{'q': 'What is your nationality?', 'src': 'bank', 'k': 'nat'},
                               {'q': 'Full name', 'src': 'rz', 'v': 'Alex Chen'},
                               {'q': 'How did you hear about us?', 'src': 'bank', 'k': 'src'}]}},
        U: {'apply': {}},
    }


def radio(label, on, group='How did you hear about us?'):
    """一組 radio:瀏覽器給這一組的名稱(fieldset 的 legend)就是題目。"""
    return {'label': label, 'name': 'hear', 'type': 'radio', 'value': label if on else '', 'group': group, 'choiceControl': True}


class Plan(unittest.TestCase):
    def test_all_form_questions_remain_in_the_material_after_question_sixty(self):
        page = {'fields': [{'label': f'Question {n}', 'type': 'text', 'value': ''}
                           for n in range(1, 62)]}
        plan = fp.build(page, {}, U, None)
        self.assertEqual(len(plan['ask']), 61)
        self.assertIn('Question 61', fp.render(plan, False))

    def test_only_blank_text_fields_are_filled_and_options_are_left_for_the_agent(self):
        page = {'fields': [
            {'label': 'Full name', 'type': 'text', 'value': ''},
            {'label': '', 'type': 'text', 'value': '', 'context': 'What is your nationality? *'},
            radio('LinkedIn', False), radio('Company career page', False),
            {'label': 'Why us?', 'type': 'textarea', 'value': ''},
            {'label': 'Salary expectation', 'type': 'text', 'value': ''},
            {'label': 'Resume', 'type': 'file', 'value': ['resume-574.pdf', 'cover.pdf']},
            {'label': 'Search', 'type': 'search', 'value': ''}]}
        plan = fp.build(page, board(), U, None)
        fill = {x['q']: x for x in plan['fill']}
        self.assertEqual(fill['Full name']['value'], 'Alex Chen')
        asked = {x['q']: x for x in plan['ask']}
        # 瀏覽器沒給這一格名稱:前面那行(多了必填記號)是不是題目由 agent 判斷,程式照實列給它(ADR-0011)
        self.assertNotIn('What is your nationality?', fill)
        self.assertEqual(asked['']['near'], 'What is your nationality? *')
        self.assertEqual(asked['How did you hear about us?']['options'], ['LinkedIn', 'Company career page'])
        # 答案有中英兩種文字:沒說這一頁用哪一種,兩種都列給 agent(ADR-0011,程式不看題目的文字)
        self.assertEqual(asked['How did you hear about us?']['value'], 'zh: 公司徵才頁 | en: Company career page')
        self.assertNotIn('pick', asked['How did you hear about us?'])
        self.assertIn('Why us?', asked)                                 # 新題目:交給 agent
        self.assertIn('等確認', asked['Salary expectation']['why'])     # 答案還在等他確認:不配
        self.assertEqual(plan['files'], [{'field': 'Resume', 'now': ['resume-574.pdf', 'cover.pdf']}])
        rows = fp.sheet_fields(plan)
        self.assertIn({'q': 'Full name', 'src': 'rz', 'value': 'Alex Chen'}, rows)
        self.assertNotIn({'q': 'What is your nationality?', 'src': 'bank', 'k': 'nat'}, rows)

    def test_every_field_is_planned_and_a_space_already_in_a_field_is_kept(self):
        """ADR-0011:程式不認「這一格是網站自己的搜尋框」,也不替頁上的值去空白。答案來源:答案庫有「Full name」,
        頁上那一格的型別是 search;國籍那一格頁上已經有一個空白。舊程式把 search 整格丟掉、把空白當成空的蓋掉。"""
        fb = board()
        fb[OTHER]['form']['f'].append({'q': '國籍', 'src': 'bank', 'k': 'nat'})
        page = {'fields': [{'label': 'Full name', 'type': 'search', 'value': ''},
                           {'label': '國籍', 'type': 'text', 'value': ' '}]}
        plan = fp.build(page, fb, U, None)
        self.assertEqual([x['q'] for x in plan['fill']], ['Full name'])
        self.assertEqual([(x['q'], x['now']) for x in plan['observed']], [('國籍', ' ')])

    def test_nonempty_text_and_selected_options_are_observed_without_being_judged(self):
        fb = board()
        fb[OTHER]['form']['f'].append({'q': '國籍', 'src': 'bank', 'k': 'nat'})
        fb[OTHER]['form']['f'].append({'q': '從哪裡得知?', 'src': 'bank', 'k': 'src'})
        page = {'fields': [{'label': '國籍', 'type': 'text', 'value': '台北'},
                           radio('LinkedIn', False, '從哪裡得知?'), radio('公司徵才頁', True, '從哪裡得知?')]}
        plan = fp.build(page, fb, U, None)
        self.assertEqual(plan['fill'], [])
        self.assertEqual(plan['observed'][0]['now'], '台北')
        self.assertEqual(plan['ask'][0]['now'], '公司徵才頁')
        self.assertTrue(any(x['q'] == '國籍' and x['k'] == 'nat' for x in fp.sheet_fields(plan)))

    def test_an_option_list_without_the_answer_goes_to_the_agent(self):
        page = {'fields': [radio('Referral', False), radio('Job board', False)]}
        plan = fp.build(page, board(), U, None)
        self.assertEqual(plan['fill'], [])
        self.assertEqual(plan['ask'][0]['options'], ['Referral', 'Job board'])

    def test_text_before_a_field_is_not_its_question(self):
        # 2026-10-07 Greenhouse 實測:「Country」前面是上一題 Phone,配成電話號碼;動機題前面是上傳欄的檔名。
        # 格子的題目只認瀏覽器給的名稱;沒有名稱的那一格前面雖然是 Phone,程式也不填,交給 agent(ADR-0011)
        fb = board()
        fb[OTHER]['form']['f'] += [{'q': 'Phone', 'src': 'rz', 'v': '+1 555 0100'}, {'q': 'Resume/CV', 'src': 'rz', 'v': 'cv.pdf'}]
        page = {'fields': [{'label': 'Country', 'type': 'combobox', 'value': '', 'context': 'Phone\nCountry'},
                           {'label': 'What interests you?', 'type': 'textarea', 'value': '', 'context': 'Resume/CV\ncv.pdf\nWhat interests you?'},
                           {'label': '', 'type': 'text', 'value': '', 'context': 'Phone'}]}
        plan = fp.build(page, fb, U, None)
        self.assertEqual(plan['fill'], [])
        self.assertEqual(sorted((x['q'], x.get('near', '')) for x in plan['ask']),
                         [('', 'Phone'), ('Country', ''), ('What interests you?', '')])

    def test_a_bilingual_answer_is_split_in_the_language_the_agent_gives(self):
        page = {'fields': [{'label': 'How did you hear about us?', 'type': 'combobox', 'value': '', 'context': 'Select...'}]}
        plan = fp.build(page, board(), U, 'en')
        self.assertEqual((plan['fill'][0]['q'], plan['fill'][0]['value']),
                         ('How did you hear about us?', 'Company career page'))
        plan = fp.build(page, board(), U, None)
        self.assertEqual(plan['fill'], [])
        self.assertIn('--lang', plan['ask'][0]['why'])

    def test_check_lists_only_available_answers_for_blank_fields(self):
        page = {'fields': [{'label': 'Full name', 'type': 'text', 'value': 'Alex Chen'}]}
        plan = fp.build(page, board(), U, None)
        self.assertIn('仍空白、尚未帶入的可用答案欄:0', fp.render(plan, True))
        self.assertEqual(plan['observed'][0]['now'], 'Alex Chen')


if __name__ == '__main__':
    unittest.main()


class ExistingPageValues(unittest.TestCase):
    def test_existing_value_different_from_answer_is_kept_as_an_observation(self):
        page = {'url': U, 'fields': [{'label': 'What is your nationality?', 'type': 'text', 'value': 'Taipei'}]}
        plan = fp.build(page, board(), U, None)
        self.assertEqual(plan['fill'], [])
        self.assertEqual(plan['observed'][0]['now'], 'Taipei')
        self.assertIn({'q': 'What is your nationality?', 'src': 'bank', 'k': 'nat'}, fp.sheet_fields(plan))
        self.assertIn('頁面已有值:1', fp.render(plan, False))


class ProgramFills(unittest.TestCase):
    """配好的格子程式自己填(--fill),agent 判斷好的值也交給程式填(--set):每一格帶著讀頁時的 backend、照種類一種填法。"""

    def test_blank_text_is_filled_but_options_are_not_selected_automatically(self):
        page = {'fields': [
            {'label': 'Full name', 'type': 'text', 'value': '', 'backend': 11},
            dict(radio('LinkedIn', False), backend=21), dict(radio('Company career page', False), backend=22)]}
        plan = fp.build(page, board(), U, 'en')
        self.assertEqual([(x['q'], x['backend'], fp.how(x)) for x in plan['fill']],
                         [('Full name', 11, 'text')])
        hear = plan['ask'][0]
        self.assertEqual((hear['options'], hear['backends'], hear['value']),
                         (['LinkedIn', 'Company career page'], [21, 22], 'Company career page'))
        self.assertNotIn('pick', hear)
        for kind, way in (('select-one', 'select'), ('combobox', 'combo'), ('textarea', 'text'), ('checkbox', 'click')):
            self.assertEqual(fp.how({'type': kind}), way)

    def test_the_agents_decided_values_go_to_the_question_it_was_asked(self):
        page = {'fields': [{'label': 'Why us?', 'type': 'textarea', 'value': '', 'backend': 31},
                           dict(radio('Yes', False, 'Do you need a visa?'), name='visa', backend=41),
                           dict(radio('No', False, 'Do you need a visa?'), name='visa', backend=42)]}
        plan = fp.build(page, board(), U, None)
        picked, miss = fp.chosen(plan, [{'q': 'Why us?', 'value': 'Because'}, {'q': 'Do you need a visa?', 'value': 'No'},
                                        {'q': 'Not on this page', 'value': 'x'}, {'q': 'Do you need a visa?', 'value': 'Maybe'}], page, board(), U, None)
        self.assertEqual([(x['backend'], x.get('pick') or x['value']) for x in picked], [(31, 'Because'), (42, 'No')])
        self.assertEqual(len(miss), 2)


@unittest.skipUnless(os.environ.get('JOBSALVO_EGO_INTEGRATION') == '1', '真 ego 填表需明確啟用')
class ProgramFillsOnARealPage(unittest.TestCase):
    """真 ego、本機假頁:程式只填空白文字欄,選項留給 agent 決定。"""
    HTML = '''<form>
<label>Full name <input name=name></label>
<label>Why us? <textarea name=why></textarea></label>
<label>Country <select name=country><option value="">--</option><option value=tw>Taiwan</option><option value=jp>Japan</option></select></label>
<fieldset><legend>How did you hear about us?</legend>
<label><input type=radio name=hear value=li>LinkedIn</label><label><input type=radio name=hear value=cc>Company career page</label></fieldset>
<label>City <input id=city role=combobox aria-autocomplete=list aria-expanded=false autocomplete=off></label>
<ul id=opts role=listbox hidden></ul>
</form><script>
const c=document.getElementById('city'), ul=document.getElementById('opts'); let chosen='';
c.addEventListener('input',()=>{const v=c.value.toLowerCase(); ul.innerHTML='';
  ['Taipei','Tainan','Tokyo'].filter(x=>v&&x.toLowerCase().startsWith(v)).forEach(x=>{const li=document.createElement('li');
    li.setAttribute('role','option'); li.textContent=x; li.addEventListener('mousedown',e=>{e.preventDefault(); c.value=x; chosen=x; ul.hidden=true;});
    ul.appendChild(li);}); ul.hidden=!ul.children.length;});
c.addEventListener('blur',()=>{if(c.value!==chosen)c.value='';});
</script>'''

    def test_blank_text_fields_are_filled_and_options_remain_for_the_agent(self):
        from unittest.mock import patch
        from urllib.parse import quote
        import chrome_door as cd
        fb = {'__ans__': [{'k': 'name', 'q': 'Full name', 'v': 'Alex Chen'}, {'k': 'why', 'q': 'Why us?', 'v': 'I like it'},
                          {'k': 'country', 'q': 'Country', 'v': 'Taiwan'},
                          {'k': 'hear', 'q': 'How did you hear about us?', 'v': 'Company career page'},
                          {'k': 'city', 'q': 'City', 'v': 'Taipei'}], U: {'apply': {}}}
        with patch.object(cd, 'ego_bin', _env.REAL_EGO_BIN), cd.test_workspaces():
            door = cd.EgoDoor()
            opened = door.open_for_agent('data:text/html;charset=utf-8,' + quote(self.HTML))
            tab = opened['tab_id']
            plan = fp.build(door.read_page(tab), fb, U, None)
            self.assertEqual(len(plan['fill']), 4, plan)
            done = door.fill_fields(tab, [{'backend': x['backend'], 'how': fp.how(x), 'value': x.get('pick') or x['value']}
                                          for x in plan['fill']])
            self.assertTrue(all(r['ok'] for r in done), done)
            after = fp.build(door.read_page(tab), fb, U, None)
            self.assertEqual(after['fill'], [], after)
            self.assertEqual(len(after['observed']), 4)
            self.assertEqual(len(after['ask']), 1)
            door.release(None)


@unittest.skipUnless(os.environ.get('JOBSALVO_EGO_INTEGRATION') == '1', '真 ego 讀頁需明確啟用')
class HiddenFormIsNotRead(unittest.TestCase):
    def test_a_form_still_behind_the_login_gives_no_boxes_but_a_hidden_attach_input_still_counts(self):
        # 假網站場景:要本人登入時表單整張藏著,讀頁不能把它的上傳欄當成這一頁的格子;
        # 畫面上那一塊裡藏起來的上傳欄(「附加檔案」按鈕後面那一個)照算
        from unittest.mock import patch
        from urllib.parse import quote
        import chrome_door as cd
        gate = '<section><h2>Sign in required</h2></section><form hidden><label>Name <input></label><input type=file></form>'
        shown = '<form><label>Name <input></label><button type=button>Attach</button><input type=file style="display:none"></form>'
        with patch.object(cd, 'ego_bin', _env.REAL_EGO_BIN), cd.test_workspaces():
            door = cd.EgoDoor()
            tab = door.open_for_agent('data:text/html;charset=utf-8,' + quote(gate))['tab_id']
            self.assertEqual(door.read_page(tab)['fields'], [])
            door.release(None)
            door = cd.EgoDoor()
            tab = door.open_for_agent('data:text/html;charset=utf-8,' + quote(shown))['tab_id']
            self.assertEqual([f['type'] for f in door.read_page(tab)['fields']], ['text', 'file'])
            door.release(None)

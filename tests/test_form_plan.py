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
                               {'q': 'Full name ✱', 'src': 'rz', 'v': 'Alex Chen'},
                               {'q': 'How did you hear about us?', 'src': 'bank', 'k': 'src'}]}},
        U: {'apply': {}},
    }


def radio(label, on, ctx='How did you hear about us?'):
    return {'label': label, 'name': 'hear', 'type': 'radio', 'value': label if on else '', 'context': ctx, 'choiceControl': True}


class Plan(unittest.TestCase):
    def test_questions_answered_on_another_form_are_filled_from_the_bank_or_the_resume(self):
        page = {'fields': [
            {'label': 'Full name', 'type': 'text', 'value': ''},
            {'label': '', 'type': 'text', 'value': '', 'context': 'What is your nationality? *'},
            radio('LinkedIn', False), radio('Company career page', False),
            {'label': 'Why us?', 'type': 'textarea', 'value': ''},
            {'label': 'Salary expectation', 'type': 'text', 'value': ''},
            {'label': 'Resume', 'type': 'file', 'value': []},
            {'label': 'Search', 'type': 'search', 'value': ''}]}
        plan = fp.build(page, board(), U)
        fill = {x['q']: x for x in plan['fill']}
        self.assertEqual(fill['Full name']['value'], 'Alex Chen')
        self.assertEqual((fill['What is your nationality?']['value'], fill['What is your nationality?']['k']), ('Taiwan', 'nat'))
        self.assertEqual(fill['How did you hear about us?']['pick'], 'Company career page')   # 答案原文的一段
        asked = {x['q']: x['why'] for x in plan['ask']}
        self.assertIn('Why us?', asked)                                    # 新題目:交給 agent
        self.assertIn('等確認', asked['Salary expectation'])               # 答案還在等他確認:不配
        self.assertEqual(len(plan['files']), 1)
        rows = fp.sheet_fields(plan)
        self.assertIn({'q': 'Full name', 'src': 'rz', 'value': 'Alex Chen'}, rows)
        self.assertIn({'q': 'What is your nationality?', 'src': 'bank', 'k': 'nat'}, rows)

    def test_a_chinese_question_gets_the_chinese_answer_and_filled_ones_are_left_alone(self):
        fb = board()
        fb[OTHER]['form']['f'].append({'q': '國籍', 'src': 'bank', 'k': 'nat'})
        page = {'fields': [{'label': '國籍', 'type': 'text', 'value': '台灣'},
                           radio('LinkedIn', False, '從哪裡得知?'), radio('公司徵才頁', True, '從哪裡得知?')]}
        fb[OTHER]['form']['f'].append({'q': '從哪裡得知?', 'src': 'bank', 'k': 'src'})
        plan = fp.build(page, fb, U)
        self.assertEqual(plan['fill'], [])
        self.assertEqual(len(plan['done']), 2)                              # 已經對了:不用動

    def test_an_option_list_without_the_answer_goes_to_the_agent(self):
        page = {'fields': [radio('Referral', False), radio('Job board', False)]}
        plan = fp.build(page, board(), U)
        self.assertEqual(plan['fill'], [])
        self.assertEqual(plan['ask'][0]['options'], ['Referral', 'Job board'])

    def test_text_before_a_field_that_belongs_to_the_previous_question_is_not_its_question(self):
        # 2026-10-07 Greenhouse 實測:「Country」前面是上一題 Phone,配成電話號碼;動機題前面是上傳欄的檔名
        fb = board()
        fb[OTHER]['form']['f'] += [{'q': 'Phone', 'src': 'rz', 'v': '+1 555 0100'}, {'q': 'Resume/CV', 'src': 'rz', 'v': 'cv.pdf'}]
        page = {'fields': [{'label': 'Country', 'type': 'combobox', 'value': '', 'context': 'Phone\nCountry'},
                           {'label': 'What interests you?', 'type': 'textarea', 'value': '', 'context': 'Resume/CV\ncv.pdf\nWhat interests you?'},
                           {'label': '', 'type': 'text', 'value': '', 'context': 'Phone'}]}
        plan = fp.build(page, fb, U)
        self.assertEqual([x['value'] for x in plan['fill']], ['+1 555 0100'])     # 只有真的是 Phone 那一格
        self.assertEqual(sorted(x['q'] for x in plan['ask']), ['Country', 'What interests you?'])

    def test_a_bilingual_answer_gives_the_half_in_the_forms_language(self):
        page = {'fields': [{'label': 'How did you hear about us?', 'type': 'combobox', 'value': '', 'context': 'Select...'}]}
        (x,) = fp.build(page, board(), U)['fill']
        self.assertEqual((x['q'], x['value']), ('How did you hear about us?', 'Company career page'))

    def test_check_lists_only_what_is_still_wrong(self):
        page = {'fields': [{'label': 'Full name', 'type': 'text', 'value': 'Alex Chen'}]}
        self.assertIn('0 個就是全部對了):0', fp.render(fp.build(page, board(), U), check=True))


if __name__ == '__main__':
    unittest.main()


class PlanAndCheckAgree(unittest.TestCase):
    """配答案(form_plan)和驗收(page_problems)用同一支題目綁定、同一支比對:計畫說「已經對了」的,驗收一定也說對上;
    計畫說還要填的,驗收一定擋。以前兩邊各寫一套,真實網站每多一種長相就只修到撞到的那一邊。"""

    def pages(self):
        filled = lambda **kw: dict({'type': 'text', 'value': ''}, **kw)
        yield 'bilingual radio', [radio('LinkedIn', False), radio('Company career page', True)]
        yield 'option labels before the inputs', [radio('LinkedIn', False, 'How did you hear about us?\nLinkedIn\nCompany career page'),
                                                  radio('Company career page', True, 'How did you hear about us?\nLinkedIn\nCompany career page')]
        yield 'fieldset legend', [dict(radio('LinkedIn', False, 'Phone'), group='How did you hear about us?'),
                                  dict(radio('Company career page', True, 'Phone'), group='How did you hear about us?')]
        yield 'bilingual text', [filled(label='How did you hear about us?', value='Company career page')]
        yield 'question in the line before, not the one before that', [
            filled(label='', context='Full name ✱\nAlex Chen\nWhat is your nationality? *', value='Taiwan')]
        yield 'chinese', [filled(label='你的國籍', value='台灣')]

    def test_plan_done_means_check_passes_and_plan_fill_means_check_blocks(self):
        import apply_tab
        for name, fields in self.pages():
            with self.subTest(name):
                fb = board()
                page = {'url': U, 'fields': fields, 'lines': []}
                plan = fp.build(page, fb, U)
                self.assertEqual((plan['fill'], plan['ask']), ([], []))
                fb[U]['form'] = {'f': fp.sheet_fields(plan)}
                self.assertEqual(apply_tab.page_problems(page, fb, U), [])
                for f in fields:                                    # 每一格都改成錯的
                    f['value'] = 'Referral' if f['type'] != 'radio' else ''
                    f.pop('shown', None)
                fields[0]['value'] = fields[0]['label'] if fields[0]['type'] == 'radio' else 'Referral'
                self.assertTrue(fp.build(page, board(), U)['fill'])
                self.assertTrue(apply_tab.page_problems(page, fb, U))


class QuestionBinding(unittest.TestCase):
    def test_a_custom_menus_shown_text_between_heading_and_box_does_not_hide_the_question_at_check(self):
        # 104 現場 DOM:「自我推薦信」標題、自製選單顯示的「Saved cover」、再來才是沒有名稱的文字框
        import apply_tab
        box = {'label': '', 'type': 'textarea', 'value': '', 'context': '選擇履歷\nKnown fixed profile\n自我推薦信\nSaved cover'}
        self.assertEqual(apply_tab.questions([box]), ['Saved cover'])             # 配答案:只認最近的,配不到交給 agent
        self.assertEqual(apply_tab.bound([box], '自我推薦信', {apply_tab.qkey('自我推薦信')}), [box])   # 驗收找得到
        recorded = {apply_tab.qkey('選擇履歷'), apply_tab.qkey('自我推薦信')}
        self.assertEqual(apply_tab.bound([box], '選擇履歷', recorded), [])     # 中間隔著表單記的別題:不是它

    def test_a_line_that_names_another_box_is_not_this_boxs_question(self):
        # Greenhouse:國碼選單前面是電話那一題,電話那一格自己有名稱;國碼選單不能拿它當題目
        import apply_tab
        code = {'label': '', 'type': 'combobox', 'value': '', 'context': 'Phone\n+886 912 345 678\nCountry', 'choiceControl': True}
        phone = {'label': 'Phone', 'type': 'tel', 'value': ''}
        self.assertEqual(apply_tab.questions([phone, code])[1], 'Country')
        self.assertEqual(apply_tab.bound([phone, code], 'Phone', {apply_tab.qkey('Phone')}), [phone])


class ProgramFills(unittest.TestCase):
    """配好的格子程式自己填(--fill),agent 判斷好的值也交給程式填(--set):每一格帶著讀頁時的 backend、照種類一種填法。"""

    def test_each_kind_of_box_has_one_way_to_fill_and_carries_its_element(self):
        page = {'fields': [
            {'label': 'Full name', 'type': 'text', 'value': '', 'backend': 11},
            dict(radio('LinkedIn', False), backend=21), dict(radio('Company career page', False), backend=22)]}
        plan = fp.build(page, board(), U)
        fill = {x['q']: x for x in plan['fill']}
        self.assertEqual((fill['Full name']['backend'], fp.how(fill['Full name'])), (11, 'text'))
        hear = fill['How did you hear about us?']
        self.assertEqual((hear['backend'], hear['pick'], fp.how(hear)), (22, 'Company career page', 'click'))
        for kind, way in (('select-one', 'select'), ('combobox', 'combo'), ('textarea', 'text'), ('checkbox', 'click')):
            self.assertEqual(fp.how({'type': kind}), way)

    def test_the_agents_decided_values_go_to_the_question_it_was_asked(self):
        page = {'fields': [{'label': 'Why us?', 'type': 'textarea', 'value': '', 'backend': 31},
                           dict(radio('Yes', False, 'Do you need a visa?'), name='visa', backend=41),
                           dict(radio('No', False, 'Do you need a visa?'), name='visa', backend=42)]}
        plan = fp.build(page, board(), U)
        picked, miss = fp.chosen(plan, [{'q': 'Why us?', 'value': 'Because'}, {'q': 'Do you need a visa?', 'value': 'No'},
                                        {'q': 'Not on this page', 'value': 'x'}, {'q': 'Do you need a visa?', 'value': 'Maybe'}])
        self.assertEqual([(x['backend'], x.get('pick') or x['value']) for x in picked], [(31, 'Because'), (42, 'No')])
        self.assertEqual(len(miss), 2)


@unittest.skipUnless(os.environ.get('JOBSALVO_EGO_INTEGRATION') == '1', '真 ego 填表需明確啟用')
class ProgramFillsOnARealPage(unittest.TestCase):
    """真 ego、本機假頁:每一種格子程式都填得上,填完讀回來配答案說「已經對了」。
    自動完成欄照常見寫法:打字才跳建議清單,點了建議項才算數(離開時沒選就清掉)。"""
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

    def test_every_kind_of_box_is_filled_and_reads_back_as_done(self):
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
            plan = fp.build(door.read_page(tab), fb, U)
            self.assertEqual(len(plan['fill']), 5, plan)
            done = door.fill_fields(tab, [{'backend': x['backend'], 'how': fp.how(x), 'value': x.get('pick') or x['value']}
                                          for x in plan['fill']])
            self.assertTrue(all(r['ok'] for r in done), done)
            name = next(x for x in plan['fill'] if x['q'] == 'Full name')
            door.fill_fields(tab, [{'backend': name['backend'], 'how': 'click', 'value': ''}])   # 自動完成欄失焦:沒點建議項的話這時會被清掉
            after = fp.build(door.read_page(tab), fb, U)
            self.assertEqual(after['fill'], [], after)
            self.assertEqual(len(after['done']), 5)
            door.release(None)


@unittest.skipUnless(os.environ.get('JOBSALVO_EGO_INTEGRATION') == '1', '真 ego 讀頁需明確啟用')
class HiddenFormIsNotRead(unittest.TestCase):
    def test_a_form_still_behind_the_login_gives_no_boxes_but_a_hidden_attach_input_still_counts(self):
        # apply_accept 的考題:要本人登入時表單整張藏著,讀頁不能把它的上傳欄當成這一頁的格子;
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

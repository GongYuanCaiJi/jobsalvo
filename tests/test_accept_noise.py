"""頁面變形開關開著,重跑四件任務的驗收測試(#776):頁面怎麼變,程式的結果都不變。

Hypothesis 產生變形方案(page_noise.Noise);固定隨機種子(derandomize),CI 每次抽同一批。每個方案把
tests/test_accept_{sync,fill,progress,find}.py 的每條測試重跑一遍,斷言原樣不動。紅了 Hypothesis 把方案縮到最小
(段落、分頁往 0 縮),訊息列最小方案與紅的測試。

幾類測試的斷言本來就釘著頁面的原樣,不是程式判斷頁面:
  tabs:斷言逐一列舉工作區的每個分頁(列了哪幾頁、拍了幾次、確認拍幾張),多開一個分頁它們就該不同;這些測試不多開分頁,其餘變形照開。
  diff:斷言列的是兩次拍之間逐段的增減,兩次之間多插一段雜訊它們就該多一筆;這些測試不插段間雜訊。
  form:斷言列舉表單的每個欄位,多一個雜訊欄位它們就該不同;這些測試不加雜訊欄位。
  name:斷言某個檔名「沒在頁上」,而雜訊字典(注錯補丁的字)裡有字真的含那個檔名;這些測試不用注錯補丁的字。
  text:斷言把頁上的字逐字比對成印出來的材料或存進卡片的原文與標題(編號、段數、整段文字),動一個字它們就該不同;這些測試不變形。
分類寫在 PINNED;其他測試沒有任何豁免。
"""
import dataclasses
import io
import os
import re
import sys
import unittest
import unittest.mock

from hypothesis import HealthCheck, Phase, example, given, settings  # noqa: E402

import fake_browser  # noqa: E402
import page_fetch  # noqa: E402
import page_noise  # noqa: E402
import test_accept_find  # noqa: E402
import test_accept_fill  # noqa: E402
import test_accept_progress  # noqa: E402
import test_accept_sync  # noqa: E402

ORIGINAL_PAGE_FETCH = page_fetch.main
MODULES = (test_accept_sync, test_accept_fill, test_accept_progress, test_accept_find)
EXAMPLES = int(os.environ.get('JOBSALVO_NOISE_EXAMPLES', 3))
INNER_EXAMPLES = int(os.environ.get('JOBSALVO_NOISE_INNER_EXAMPLES', 20))   # 驗收測試裡自己的 Hypothesis 例數,只在這支重跑時降低
PINNED = {     # 測試 → 它釘住的是哪幾樣(tabs、text、diff、form;說明在檔頭)
    'test_accept_sync.PairingTest.test_every_page_is_listed_with_its_true_relation_and_marks': {'tabs', 'diff'},
    'test_accept_sync.PairingTest.test_several_pages_each_keep_every_relation': {'tabs'},
    'test_accept_sync.MomentTest.test_first_before_and_last_after_count_and_every_call_is_recorded': {'tabs'},
    'test_accept_sync.TextTest.test_listed_changes_equal_the_paragraph_difference': {'tabs', 'diff'},
    'test_accept_fill.ConfirmTest.test_confirm_screen_lists_what_changed_since_the_fill': {'tabs', 'diff'},
    'test_accept_sync.CrossTabTest.test_paragraphs_do_not_cancel_across_tabs': {'diff'},
    'test_accept_sync.RestoreTest.test_restore_tells_the_agent_every_paragraph_to_put_back': {'diff'},
    'test_accept_sync.SheetTest.test_each_said_change_is_checked_against_what_changed': {'diff'},
    'test_accept_sync.SheetTest.test_any_cell_shape_is_listed_as_the_agent_wrote_it': {'diff'},
    'test_accept_sync.AttachmentTest.test_attachment_paragraph_changes_are_listed': {'diff'},
    'test_accept_fill.FixedProfileTest.test_fixed_profile_pages_are_published_by_exact_url': {'diff'},
    'test_accept_fill.FormPlanTest.test_program_fills_only_blank_text_fields_whose_name_matches_exactly': {'form'},
    'test_accept_fill.FormPlanToolTest.test_a_question_nobody_answered_is_asked_and_the_rest_still_fill': {'form'},
    'test_accept_fill.FormPlanToolTest.test_every_way_the_agent_calls_the_form_tool': {'form'},
    'test_accept_sync.IntendedTest.test_intended_text_marks_follow_what_the_program_read': {'diff'},
    'test_accept_fill.UploadTest.test_uploaded_names_and_profile_choice': {'name'},
    'test_accept_sync.RestoreTest.test_restore_round_lists_its_own_comparison': {'tabs', 'diff'},
    'test_accept_progress.PromptTest.test_material_and_single_read': {'text'},
    'test_accept_find.AddJobTest.test_a_held_page_whose_shot_is_gone_is_written_with_the_paste_and_says_so': {'text'},
    'test_accept_find.AddJobTest.test_one_paste_from_entry_to_card': {'text'},
    'test_accept_find.FieldTest.test_numbers_that_point_at_the_wrong_paragraphs': {'text'},
    'test_accept_find.JudgeMaterialTest.test_resume_choices_and_one_block_per_page': {'text'},
    'test_accept_find.PageFetchTest.test_prints_numbered_material_or_why': {'text'},
    'test_accept_find.WholeRoundTest.test_the_whole_round': {'text'},
}


def tests():
    loader = unittest.defaultTestLoader

    def flatten(suite):
        for item in suite:
            yield from flatten(item) if isinstance(item, unittest.TestSuite) else [item]
    for module in MODULES:
        yield from flatten(loader.loadTestsFromModule(module))


def limited(noise, kinds):
    kinds = kinds or set()
    if 'text' in kinds:
        return dataclasses.replace(noise, head=0, tail=0, repeat=0, retitle=False, tabs=0, old=False, between=False,
                                   sameurl=False, fields=0)
    return dataclasses.replace(noise, **{
        **({'tabs': 0, 'sameurl': False} if 'tabs' in kinds else {}),
        **({'between': False} if 'diff' in kinds else {}),
        **({'fields': 0} if 'form' in kinds else {}),
        **({'old': False} if 'name' in kinds else {})})


def brief(trace):
    """紅的測試只留例外種類與出處行,不印斷言兩邊的內容(那裡會有整頁原文)。"""
    lines = trace.strip().splitlines()
    kind = next((line.split(':', 1)[0] for line in reversed(lines) if re.match(r'^[A-Za-z_.]*(Error|Exception)\b', line)), 'Failure')
    where = next((line.strip() for line in reversed(lines) if 'tests/test_accept' in line and line.strip().startswith('File')), '')
    return f'{kind} {where.removeprefix("File ").split(", in")[0]}'.strip()


def checked_page_fetch(argv=None, browser=None):
    """agent 叫的 page_fetch:印出來的編號材料要有瀏覽器給的那一頁的每一段(fake_browser.faithful)。"""
    out = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = Tee(real_stdout, out)
    try:
        code = ORIGINAL_PAGE_FETCH(argv, browser=browser)
    finally:
        sys.stdout = real_stdout
    for url, lines in getattr(browser, 'fetch_parts', {}).items():
        fake_browser.faithful(out.getvalue(), lines, f'page_fetch 印的 {url}')
    return code


class Tee:
    def __init__(self, *targets):
        self.targets = targets

    def write(self, text):
        for target in self.targets:
            target.write(text)

    def flush(self):
        for target in self.targets:
            target.flush()


def red(noise):
    """這個方案下紅的測試:[(測試, 訊息)]。"""
    found = []
    saved = fake_browser.NOISE, fake_browser.SALT
    try:
        for case in tests():
            name = case.id()
            fake_browser.NOISE = noise and limited(noise, PINNED.get(name))
            fake_browser.SALT = name
            result = unittest.TestResult()
            with unittest.mock.patch('sys.stdout', io.StringIO()), \
                    unittest.mock.patch.object(page_fetch, 'main', checked_page_fetch), \
                    unittest.mock.patch.object(test_accept_sync, 'EXAMPLES', INNER_EXAMPLES):   # 只在這裡降,不動模組本身
                case.run(result)
            found += [(name, brief(text)) for _case, text in (*result.failures, *result.errors)]
    finally:
        fake_browser.NOISE, fake_browser.SALT = saved
    return found


def red_plain():
    """開關關著的基準:同一批測試,不加變形。"""
    return red(None)


class PageFetchNoiseTest(unittest.TestCase):
    """page_fetch 讀變形過的頁:印出的編號材料要依序、一字不差含瀏覽器給的每一段(PageFetchTest 不變形,這條專考變形)。"""

    def printed(self, noise):
        import test_accept_find as find
        browser = fake_browser.FakeBrowser(fetched={find.URL: find.fetched(find.URL, 'ok')})
        out = io.StringIO()
        with unittest.mock.patch.object(fake_browser, 'NOISE', noise), unittest.mock.patch.object(fake_browser, 'SALT', 'fetch'), \
                unittest.mock.patch('sys.stdout', out):
            ORIGINAL_PAGE_FETCH([find.URL], browser=browser)
        return out.getvalue(), browser.fetch_parts[find.URL]

    def test_every_paragraph_the_browser_gave_is_printed_in_order(self):
        for seed in range(1, 6):
            text, parts = self.printed(page_noise.from_env(str(seed)))
            self.assertGreater(len(parts), len(__import__('test_accept_find').PARTS), seed)    # 真的有雜訊段
            with unittest.mock.patch.object(fake_browser, 'NOISE', page_noise.from_env(str(seed))):
                fake_browser.faithful(text, parts, 'page_fetch')

    def test_a_dropped_paragraph_is_caught(self):
        text, parts = self.printed(page_noise.from_env('1'))
        kept = [line for line in text.split('\n') if line.strip() != f'{len(parts) // 2}. {parts[len(parts) // 2 - 1]}']
        with unittest.mock.patch.object(fake_browser, 'NOISE', page_noise.from_env('1')), self.assertRaises(AssertionError):
            fake_browser.faithful('\n'.join(kept), parts, 'page_fetch')


class NoisyAcceptanceTest(unittest.TestCase):
    def test_the_plain_runs_keep_their_own_example_count(self):
        """這支重跑時才降內層例數;discover 載入後 test_accept_sync 自己的還是原本的(預設 200)。"""
        self.assertEqual(test_accept_sync.EXAMPLES, int(os.environ.get('JOBSALVO_ACCEPT_EXAMPLES', 200)))


    @settings(max_examples=EXAMPLES, derandomize=True, deadline=None, database=None,
              phases=(Phase.explicit, Phase.generate, Phase.shrink),
              suppress_health_check=list(HealthCheck))
    @example(page_noise.from_env('5:where=0'))
    @example(page_noise.from_env('5:where=1'))
    @example(page_noise.from_env('5:where=2'))
    @example(page_noise.from_env('7:where=1,tabs=0,sameurl=1'))
    @given(page_noise.noises())
    def test_every_acceptance_test_passes_under_any_page_noise(self, noise):
        wrong = red(noise)
        self.assertEqual(wrong, [], f'變形方案 {noise}')


if __name__ == '__main__':
    unittest.main()

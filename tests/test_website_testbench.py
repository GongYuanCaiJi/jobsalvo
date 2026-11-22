import os
import unittest
from unittest.mock import patch

from website_testbench import WebsiteTestBench
import dev_browser


class WebsiteTestBenchContract(unittest.TestCase):

    def test_the_fake_site_keeps_a_record_of_every_page_it_served(self):
        import web.fetch as page_fetch
        with WebsiteTestBench(pages={'/job': '<!doctype html><title>Job</title><p>Engineer</p>'}) as bench:
            bench.open_page('/job')
            bench.server_state()
            with patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '1'}):
                page_fetch.request_page(bench.server.url('/job?ref=1'))
            page_fetch_url = bench.server.url('/missing')
            with patch.dict(os.environ, {'JOBSALVO_TEST_ALLOW_LOOPBACK_FETCH': '1'}):
                page_fetch.request_page(page_fetch_url)

            seen = [(row['method'], row['url'], row['status']) for row in bench.server.requests()]
            self.assertIn(('GET', bench.server.url('/job'), 200), seen)
            self.assertIn(('GET', bench.server.url('/job?ref=1'), 200), seen)
            self.assertIn(('GET', page_fetch_url, 404), seen)
            self.assertFalse(any(url.endswith('/state') for _method, url, _status in seen))
            self.assertTrue(all(row['at'] for row in bench.server.requests()))

    def test_a_page_whose_screenshots_fail_is_still_read_and_the_failure_is_kept(self):
        import evidence
        from web import seen
        with WebsiteTestBench(pages={'/profile': '<!doctype html><p>profile text</p>'}) as bench:
            page_id = bench.open_page('/profile')
            if isinstance(bench.browser, dev_browser.Ego):
                self.skipTest('ego 模式的截圖由 ego 自己拍,測試台造不出截圖失敗')
            bench.break_screenshots(page_id, '假網站截圖失敗')
            rnd = evidence.Round('website-testbench', 'shot', board=os.path.join(bench.evidence_dir, 'b.html'))
            snapshot = bench.capture_product_profile('before', rnd.dirs)
            page, = (seen.dump(page) for page in snapshot['snapshot'].pages)
            self.assertEqual(page['status'], 'read')
            self.assertIn('假網站截圖失敗', page['shot_why'])

    def test_browser_changes_are_checked_against_the_server_state(self):
        path_before = os.environ.get('PATH')
        with WebsiteTestBench(initial_text='old text') as bench:
            page_id = bench.open_page('/')
            second_page_id = bench.open_page('/')
            before = bench.read_profile_page(page_id)
            self.assertIn('old text', before['reading'])

            result = bench.run_agent_script(page_id, """async () => {
              const value = document.querySelector('[name="content"]');
              value.value = 'saved text';
              await fetch('/save', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content: value.value})});
              return {saved: value.value};
            }""")

            self.assertEqual(result, {'saved': 'saved text'})
            state = bench.server_state()
            self.assertEqual(state['content'], 'saved text')
            self.assertEqual(state['saved'], True)
            self.assertEqual(state['changes'], [{'before': 'old text', 'after': 'saved text'}])
            if isinstance(bench.browser, dev_browser.Ego):
                events = bench.bridge_events()
                shots = [event for event in events if event['kind'] == 'shot']
                self.assertEqual(len(shots), 2, events)
                workspace = bench.agent_page_id(page_id).split(':')[0]
                self.assertTrue(all(str(event['workspace']) == workspace for event in shots))
                self.assertEqual({event['page'] for event in shots}, {
                    bench.agent_page_id(page_id).split(':')[1],
                    bench.agent_page_id(second_page_id).split(':')[1],
                })
                self.assertTrue(all(bench.bridge_shot_path(event['file']).is_file() for event in shots))
                self.assertTrue(all(event['screenshot_ms'] >= 0 for event in shots))
                result = bench.run_agent_script(
                    page_id, 'async () => ({completed: true})', finish_after=True)
                self.assertEqual(result, {'completed': True})
                failures = [event for event in bench.bridge_events()
                            if event.get('what') == 'ego_screenshot_failed']
                self.assertEqual(len(failures), 1, bench.bridge_events())
                self.assertTrue(failures[0]['reason'])
        self.assertEqual(os.environ.get('PATH'), path_before)

    def test_page_material_limit_is_per_page(self):
        ordinary = 'x' * 30_000
        oversized = 'y' * 100_001
        pages = {
            '/first': f'<!doctype html><title>First</title><p>{ordinary}</p>',
            '/second': f'<!doctype html><title>Second</title><p>{ordinary}</p>',
            '/large': f'<!doctype html><title>Large</title><p>{oversized}</p>',
        }
        with WebsiteTestBench(pages=pages) as bench:
            for path in pages:
                bench.open_page(path)

            read = {page['url']: page for page in bench.read_open_pages()}
            self.assertEqual('readable', read[bench.server.url('/first')]['read_status'])
            self.assertEqual('readable', read[bench.server.url('/second')]['read_status'])
            self.assertEqual('too_large', read[bench.server.url('/large')]['read_status'])


if __name__ == '__main__':
    unittest.main()

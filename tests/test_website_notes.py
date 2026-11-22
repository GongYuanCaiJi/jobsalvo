import contextlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest import mock

import _env
import config as cf
import website_notes


class WebsiteNotesMigration(unittest.TestCase):
    def setUp(self):
        _env.use_home(self)

    def test_legacy_platform_notes_do_not_follow_the_order_of_the_users_host_list(self):
        import profile_sync
        profile_sync.set_hosts('104', ['jobs.board.example', '104.com.tw'])
        with open(cf.path('platform-notes.json'), 'w', encoding='utf-8') as f:
            json.dump({'104': ['104 筆記']}, f, ensure_ascii=False)

        self.assertEqual(['104 筆記'], [note['text'] for note in website_notes.for_url('https://104.com.tw/job/1')])
        self.assertEqual([], website_notes.for_url('https://jobs.board.example/job/1'))

    def test_legacy_platform_notes_stay_on_the_host_they_had_before_the_upgrade(self):
        # 答案來源:升級前的程式(e7ad454)實際跑同一份舊筆記的結果:104 的筆記在 104.com.tw,
        # cake 的筆記只在 cake.me,cake.com、cakeresume.com 上沒有;其他鍵照原樣當主機。
        legacy = {'104': ['104 筆記'], 'cake': ['Cake 筆記'], 'greenhouse.io': ['Greenhouse 筆記']}
        old_path = cf.path('platform-notes.json')
        with open(old_path, 'w', encoding='utf-8') as f:
            json.dump(legacy, f, ensure_ascii=False)

        old_104 = website_notes.for_url('https://104.com.tw/job/1')
        old_104_subdomain = website_notes.for_url('https://pda.104.com.tw/profile')
        old_cake = website_notes.for_url('https://cake.me/jobs/1')
        old_unknown = website_notes.for_url('https://greenhouse.io/job/1')

        self.assertEqual(['104 筆記'], [note['text'] for note in old_104])
        self.assertEqual('104.com.tw', old_104[0]['host'])
        self.assertEqual([], old_104_subdomain)
        self.assertEqual(['Cake 筆記'], [note['text'] for note in old_cake])
        self.assertEqual('cake.me', old_cake[0]['host'])
        for other in ('https://cake.com/jobs/1', 'https://cakeresume.com/jobs/1'):
            self.assertEqual([], website_notes.for_url(other))
        self.assertTrue(old_104[0]['legacy'])
        self.assertIsNone(old_104[0]['url'])
        self.assertIsNone(old_104[0]['date'])
        self.assertIsNone(old_104[0]['card_id'])
        self.assertIsNone(old_104[0]['round_id'])
        self.assertEqual('agent', old_104[0]['author'])
        self.assertEqual(['Greenhouse 筆記'], [note['text'] for note in old_unknown])
        self.assertEqual('greenhouse.io', old_unknown[0]['host'])
        self.assertIsNone(old_unknown[0]['url'])
        self.assertIsNone(old_unknown[0]['date'])
        self.assertIsNone(old_unknown[0]['card_id'])
        self.assertIsNone(old_unknown[0]['round_id'])
        self.assertEqual('agent', old_unknown[0]['author'])
        self.assertTrue(os.path.isfile(cf.path('website-notes.json')))

        with open(old_path, 'w', encoding='utf-8') as f:
            json.dump({'104': ['後來改的舊筆記']}, f, ensure_ascii=False)
        self.assertEqual(['104 筆記'], [note['text'] for note in website_notes.for_url('https://104.com.tw/job/1')])
        self.assertEqual(['Cake 筆記'], [note['text'] for note in website_notes.for_url('https://cake.me/jobs/1')])
        self.assertEqual(['Greenhouse 筆記'], [note['text'] for note in website_notes.for_url('https://greenhouse.io/job/1')])
        with open(old_path, encoding='utf-8') as f:
            self.assertEqual({'104': ['後來改的舊筆記']}, json.load(f))

    def test_a_set_of_urls_returns_each_exact_host_group(self):
        root = 'https://new-platform.example/jobs/1'
        subdomain = 'https://www.new-platform.example/jobs/2'
        website_notes.remember([{'url': root, 'note': '根主機'}])
        website_notes.remember([{'url': subdomain, 'note': '子主機'}])

        notes = website_notes.for_urls([root, subdomain, subdomain])

        self.assertEqual({'new-platform.example': ['根主機'],
                          'www.new-platform.example': ['子主機']},
                         {host: [item['text'] for item in items] for host, items in notes.items()})


    def test_parallel_note_adds_keep_both_read_modify_write_updates(self):
        barrier = threading.Barrier(2)
        original_write = website_notes._write

        def interleaved_write(data, path=None):
            with contextlib.suppress(threading.BrokenBarrierError):
                barrier.wait(timeout=0.25)
            original_write(data, path)

        with mock.patch.object(website_notes, '_write', side_effect=interleaved_write):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(website_notes.user_action, 'add', url=f'https://notes-{n}.example/jobs/1', text=f'筆記 {n}')
                           for n in (1, 2)]
                [future.result(timeout=5) for future in futures]

        self.assertEqual({'notes-1.example', 'notes-2.example'},
                         {note['host'] for note in website_notes.all_notes()})

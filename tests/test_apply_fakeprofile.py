import os
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402  測試跑在暫存資料夾

from apply_fakeprofile import FakePlatformProfile, acceptance_scenarios, sha256
import profile_sync


def _post(url, fields=None, filename=None, content=b''):
    fields = fields or {}
    if filename is None:
        body = urllib.parse.urlencode(fields).encode()
        content_type = 'application/x-www-form-urlencoded'
    else:
        body, content_type = _env.multipart(fields, [('attachment', filename, content)])
    request = urllib.request.Request(
        url, data=body, headers={'Content-Type': content_type}, method='POST',
    )
    return urllib.request.urlopen(request)


class FakePlatformProfileServer(unittest.TestCase):
    def setUp(self):
        self.files = {
            'support': b'expected support file',
            'custom': b'expected custom resume',
            'old': b'old version',
        }
        self.scenarios = acceptance_scenarios(
            self.files['support'], self.files['custom'], self.files['old'],
            'SYNTHETIC PROFILE TEXT',
        )
        self.server = FakePlatformProfile(scenarios=self.scenarios).start()

    def tearDown(self):
        self.server.stop()

    def test_acceptance_scenarios_render_only_local_profile_controls(self):
        self.assertEqual(set(self.server.scenarios), {
            'slots-full', 'extra-old', 'download-error', 'upload-error', 'normal',
        })
        full = self.server.scenarios['slots-full']
        self.assertEqual(len(full['profiles']), full['slot_limit'])
        self.assertTrue(full['requires_custom_resume'])
        self.assertIn('support.pdf', self.server._manager_html('slots-full', full))
        self.assertIn('本機假驗收頁', self.server._manager_html('normal', self.server.scenarios['normal']))
        self.assertIn('data-local-acceptance="true"', self.server._manager_html(
            'normal', self.server.scenarios['normal'],
        ))
        profile_page = self.server._profile_html(
            'normal', self.server.scenarios['normal']['profiles']['fixed'],
        )
        self.assertIn('本機假驗收頁', profile_page)
        self.assertIn('SYNTHETIC PROFILE TEXT', profile_page)
        self.assertIn('action="/upload/normal/fixed"', profile_page)
        self.assertIn('name="attachment"', profile_page)

        page = urllib.request.urlopen(self.server.url('normal')).read().decode()
        self.assertIn(self.server.manager_url('normal'), page)
        self.assertIn('沒有直接附件上傳欄', page)
        self.assertIn('本機假驗收頁', page)
        self.assertIn('name="profile_id"', page)
        self.assertIn('value="fixed"', page)
        self.assertNotIn('<input id="resume"', page)
        self.assertIn('127.0.0.1', self.server.url('normal'))
        self.assertEqual(profile_sync.platform_of(self.server.url('normal')), '104')

    def test_custom_profile_can_be_created_then_uploaded_from_its_visible_form(self):
        response = _post(
            self.server.manager_url('normal').replace('/profiles/', '/create/'),
            fields={'name': 'Synthetic acceptance profile'},
        )
        self.assertIn('/profile/normal/custom-1', response.geturl())
        page = response.read().decode()
        self.assertIn('本機假驗收頁', page)
        self.assertIn('action="/upload/normal/custom-1"', page)
        self.assertIn('name="attachment"', page)

        upload_url = self.server.profile_url('normal', 'custom-1').replace('/profile/', '/upload/')
        for filename, content in (('resume.pdf', self.files['custom']),
                                  ('support.pdf', self.files['support'])):
            upload = _post(upload_url, filename=filename, content=content)
            upload.read()
        for attachment_id, expected in (('upload-1', self.files['custom']),
                                        ('upload-2', self.files['support'])):
            downloaded = urllib.request.urlopen(
                self.server.download_url('normal', 'custom-1', attachment_id),
            ).read()
            self.assertEqual(downloaded, expected)
        events = self.server.events(case='normal')
        created = next(event for event in events if event['action'] == 'CREATE_PROFILE')
        uploads = [event for event in events if event['action'] == 'UPLOAD']
        self.assertTrue(created['ok'])
        self.assertTrue(all(event['profile_kind'] == 'custom' for event in uploads))
        self.assertEqual([event['sha256'] for event in uploads], [
            sha256(self.files['custom']), sha256(self.files['support']),
        ])

    def test_download_and_same_name_upload_record_hashes_and_replacement(self):
        url = self.server.download_url('normal', 'fixed', 'support')
        self.assertEqual(urllib.request.urlopen(url).read(), self.files['support'])
        before = self.server.fixed_snapshot('normal')

        response = _post(
            self.server.profile_url('normal', 'fixed').replace('/profile/', '/upload/'),
            filename='support.pdf', content=b'updated support',
        )
        response.read()
        after = self.server.fixed_snapshot('normal')
        events = self.server.events(case='normal')
        download = next(event for event in events if event['action'] == 'DOWNLOAD')
        upload = next(event for event in events if event['action'] == 'UPLOAD')

        self.assertEqual(download['sha256'], sha256(self.files['support']))
        self.assertEqual(upload['sha256'], sha256(b'updated support'))
        self.assertEqual(upload['previous_sha256'], sha256(self.files['support']))
        self.assertNotEqual(before, after)
        self.assertEqual(response.status, 200)

    def test_download_supports_utf8_attachment_names(self):
        filename = '附件-證明.pdf'
        profile = self.server.scenarios['normal']['profiles']['fixed']
        profile['attachments'][0]['name'] = filename

        with urllib.request.urlopen(
            self.server.download_url('normal', 'fixed', 'support'),
        ) as response:
            self.assertEqual(response.read(), self.files['support'])
            disposition = response.headers['Content-Disposition']

        self.assertTrue(disposition.isascii())
        self.assertIn('filename*=', disposition)
        self.assertIn(urllib.parse.quote(filename, safe="!#$&+-.^_`|~"), disposition)

    def test_empty_upload_is_rejected_and_never_added_to_profile(self):
        url = self.server.profile_url('normal', 'fixed').replace('/profile/', '/upload/')
        with self.assertRaises(urllib.error.HTTPError) as empty:
            _post(url, filename='', content=b'')
        self.assertEqual(empty.exception.code, 400)
        empty.exception.close()
        event = next(event for event in self.server.events(case='normal')
                     if event['action'] == 'UPLOAD')
        self.assertFalse(event['ok'])
        self.assertEqual(event['reason'], 'empty_file')
        self.assertEqual(event['sha256'], sha256(b''))
        self.assertEqual(self.server.scenarios['normal']['profiles']['fixed']['attachments'],
                         [{'id': 'support', 'name': 'support.pdf',
                           'content': self.files['support']}])

    def test_configured_slot_download_and_upload_failures_are_logged(self):
        with self.assertRaises(urllib.error.HTTPError) as full:
            _post(self.server.url('normal').replace('/apply?', '/create/slots-full?'),
                  {'name': 'custom'})
        self.assertEqual(full.exception.code, 409)
        full.exception.close()
        create = next(event for event in self.server.events(case='slots-full')
                      if event['action'] == 'CREATE_PROFILE')
        self.assertFalse(create['ok'])
        self.assertEqual(create['reason'], 'slots_full')

        with self.assertRaises(urllib.error.HTTPError) as download:
            urllib.request.urlopen(self.server.download_url('download-error', 'fixed', 'support'))
        self.assertEqual(download.exception.code, 503)
        download.exception.close()
        download_event = next(event for event in self.server.events(case='download-error')
                              if event['action'] == 'DOWNLOAD')
        self.assertIsNone(download_event['sha256'])
        self.assertEqual(download_event['stored_sha256'], sha256(self.files['support']))

        create_url = self.server.manager_url('upload-error').replace(
            '/profiles/', '/create/',
        )
        created = _post(create_url, {'name': 'custom'})
        created.read()
        with self.assertRaises(urllib.error.HTTPError) as upload:
            _post(self.server.profile_url('upload-error', 'custom-1').replace(
                '/profile/', '/upload/',
            ), filename='resume.pdf', content=self.files['custom'])
        self.assertEqual(upload.exception.code, 503)
        upload.exception.close()
        upload_event = next(event for event in self.server.events(case='upload-error')
                            if event['action'] == 'UPLOAD')
        self.assertFalse(upload_event['ok'])
        self.assertEqual(upload_event['sha256'], sha256(self.files['custom']))
        self.assertEqual(
            self.server.scenarios['upload-error']['profiles']['custom-1']['attachments'], [],
        )

    def test_delete_records_the_target_hash_and_changes_fake_state(self):
        before = self.server.fixed_snapshot('extra-old')
        url = (self.server.profile_url('extra-old', 'fixed')
               .replace('/profile/', '/delete-attachment/') + '/old-version')
        response = _post(url)
        response.read()
        event = next(event for event in self.server.events(case='extra-old')
                     if event['action'] == 'DELETE_ATTACHMENT')
        self.assertEqual(event['sha256'], sha256(self.files['old']))
        self.assertTrue(event['ok'])
        self.assertNotEqual(before, self.server.fixed_snapshot('extra-old'))


if __name__ == '__main__':
    unittest.main()

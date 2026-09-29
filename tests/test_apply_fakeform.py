import os
import re
import sys
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', 'tools')))
from apply_fakeform import FakeForm


def _file_inputs(page):
    return [re.search(r'\bname="([^"]+)"', tag).group(1)
            for tag in re.findall(r'<input\b[^>]*>', page)
            if 'type="file"' in tag]


def _submit(server, job, files):
    boundary = '----jobsalvo-test-boundary'
    parts = []
    for field, name, content in files:
        parts.extend((
            f'--{boundary}\r\n'.encode(),
            f'Content-Disposition: form-data; name="{field}"; filename="{name}"\r\n'.encode(),
            b'Content-Type: application/pdf\r\n\r\n', content, b'\r\n',
        ))
    parts.append(f'--{boundary}--\r\n'.encode())
    endpoint = server.url(job).replace('/apply?', '/submit?')
    request = urllib.request.Request(endpoint, data=b''.join(parts), method='POST', headers={
        'Content-Type': f'multipart/form-data; boundary={boundary}',
    })
    with urllib.request.urlopen(request) as response:
        response.read()
    return server.submits()[-1]['files']


class FakeFormUploads(unittest.TestCase):
    def test_one_upload_field_receives_only_the_combined_pdf(self):
        server = FakeForm().start()
        try:
            page = urllib.request.urlopen(server.url('single')).read().decode()
            self.assertEqual(_file_inputs(page), ['resume'])
            submitted = _submit(server, 'single', [('resume', 'merged.pdf', b'combined')])
            self.assertEqual({name: item['n'] for name, item in submitted.items()}, {
                'resume': 'merged.pdf',
            })
        finally:
            server.stop()

    def test_multiple_upload_fields_receive_individual_files_without_the_combined_pdf(self):
        server = FakeForm(upload_fields=2).start()
        try:
            page = urllib.request.urlopen(server.url('multiple')).read().decode()
            self.assertEqual(_file_inputs(page), ['resume', 'attachments-2'])
            submitted = _submit(server, 'multiple', [
                ('resume', 'resume.pdf', b'resume'),
                ('attachments-2', 'letter.pdf', b'attachment'),
            ])
            names = {name: item['n'] for name, item in submitted.items()}
            self.assertEqual(names, {'resume': 'resume.pdf', 'attachments-2': 'letter.pdf'})
            self.assertNotIn('merged.pdf', names.values())
        finally:
            server.stop()

    def test_resume_and_cover_letter_slots_accept_only_the_merged_resume(self):
        server = FakeForm(upload_slots=[('resume', 'Resume'), ('cover_letter', 'Cover Letter')]).start()
        try:
            page = urllib.request.urlopen(server.url('two-slots')).read().decode()
            self.assertEqual(_file_inputs(page), ['resume', 'cover_letter'])
            submitted = _submit(server, 'two-slots', [('resume', 'merged.pdf', b'combined resume')])
            self.assertEqual({name: item['n'] for name, item in submitted.items()}, {
                'resume': 'merged.pdf',
            })
        finally:
            server.stop()


if __name__ == '__main__':
    unittest.main()

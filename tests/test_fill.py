"""填表交件單由共用 Schema 產生並以同一份 Schema 收件。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import fill
from web.sheet import Context, read


class FillSchema(unittest.TestCase):
    def _example(self, schema):
        prompt = schema.format_text().splitlines()
        start = next(index for index, line in enumerate(prompt) if line.startswith('{'))
        depth = 0
        end = start
        for end in range(start, len(prompt)):
            depth += prompt[end].count('{') - prompt[end].count('}')
            if depth == 0:
                break
        return json.loads('\n'.join(prompt[start:end + 1]))

    def test_fill_and_submit_examples_read_back_to_the_same_cells(self):
        for schema in (fill.FILL_SHEET, fill.SUBMIT_SHEET):
            with self.subTest(schema=schema.task), tempfile.TemporaryDirectory() as directory:
                example = self._example(schema)
                path = os.path.join(directory, 'handoff.json')
                with open(path, 'w', encoding='utf-8') as target:
                    json.dump(example, target, ensure_ascii=False)
                sheet = read(path, schema, Context((), {}))
                self.assertIsNone(sheet.broken)
                for field in schema.fields:
                    cell = sheet.cells[field.name]
                    if field.many:
                        self.assertEqual(len(cell), len(example[field.name]))
                        for actual, expected in zip(cell, example[field.name]):
                            self.assertEqual({key: value.said for key, value in actual.items()}, expected)
                    else:
                        self.assertEqual(cell.said, example[field.name])

    def test_fill_handoff_has_required_user_and_stop_fields(self):
        names = {field.name for field in fill.FILL_SHEET.fields}
        self.assertTrue({'same_job', 'delivery', 'submitted', 'needs_user', 'stopped'} <= names)
        text = fill.FILL_SHEET.format_text()
        self.assertIn('需要使用者本人處理', text)
        self.assertIn('沒做完就停下', text)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一個或多個 evidence/_sync_profiles/<輪> 變成單頁本機驗收報告。"""
import argparse
import base64
from collections import Counter, defaultdict
import datetime
import html
import json
import os
import re
import tempfile


PENDING = {'agent 代填待確認', '範圍外有內容消失', '重新載入後與改前不同', '這一頁找不到這一段，沒放上去'}


def _events(folder):
    with open(os.path.join(folder, 'events.jsonl'), encoding='utf-8') as source:
        return [json.loads(line) for line in source if line.strip()]


def _identity(folder, rows):
    start = next((row for row in rows if row.get('kind') == 'slot_start'), None)
    if start:
        return start['platform'], start['lang'], start['variant']
    suffix = os.path.basename(folder).partition('-sync_profiles-')[2]
    match = re.fullmatch(r'(.+)_(zh|en)_(.+)', suffix)
    return match.groups() if match else (suffix or '未知平台', '未知語言', '未知份別')


def _legacy_changes(folder, rows):
    """舊輪沒有 change；用同輪 R0/R1、選取編號與 verification 重建可證明的部分。"""
    import sync_profiles as sp
    pages = {row.get('why'): row for row in rows if row.get('kind') == 'page'}
    checks = [row for row in rows if row.get('kind') == 'check' and row.get('phase') == 'compare']
    verifications = [row for row in rows if row.get('kind') == 'verification']
    changes = []
    for index, verdict in enumerate(verifications):
        before_row, after_row = pages.get(f'before-{index}'), pages.get(f'after-{index}')
        if not before_row or not after_row:
            continue
        try:
            with open(os.path.join(folder, before_row['file']), encoding='utf-8') as source:
                before = json.load(source)
            with open(os.path.join(folder, after_row['file']), encoding='utf-8') as source:
                after = json.load(source)
        except (OSError, KeyError, ValueError):
            continue
        check = next((row for row in checks if row.get('item') == verdict.get('item')
                      and row.get('n', 0) < verdict.get('n', 0)), {})
        ids = check.get('ids') or []
        try:
            selected = sp._selected_parts(sp._reading_parts(before), ids)
        except ValueError:
            selected = []
        added = sp._added_parts(before, after)
        outside = next((row.get('parts') or [] for row in rows
                        if row.get('kind') == 'outside_loss_pending' and row.get('item') == verdict.get('item')
                        and row.get('n', 0) < verdict.get('n', 0)), [])
        verdicts = [str(verdict.get('verdict') or '未判定')]
        if outside:
            verdicts.append('範圍外有內容消失')
        later = [row for row in rows if row.get('item') == verdict.get('item')
                 and row.get('n', 0) > verdict.get('n', 0)]
        if any(row.get('kind') == 'platform_autosaved' for row in later):
            verdicts.append('平台自動存檔')
            result = '平台自己存了'
        elif any(row.get('kind') == 'restored_differs_pending' for row in later):
            verdicts.append('重新載入後與改前不同')
            result = '只驗收、已撤回'
        elif pages.get(f'restored-{index}'):
            result = '只驗收、已撤回'
        elif pages.get(f'saved-{index}'):
            result = '已存上平台'
        elif sp._reading_parts(before) == sp._reading_parts(after):
            result = '平台沒變／跟原稿相同' if verdicts[0] == '照原文' else '平台沒變'
        else:
            result = '沒做成'
        changes.append({'item': verdict.get('item') or f'第 {index + 1} 處',
                        'verdicts': verdicts, 'platform_result': result,
                        'before_text': '\n'.join(sp._plain_part(part) for part in selected),
                        'after_text': '\n'.join(sp._plain_part(part) for part in (added or selected)),
                        'outside_losses': outside, 'answer_question': ''})
    return changes


def _round(folder):
    rows = _events(folder)
    changes = [row for row in rows if row.get('kind') == 'change']
    for update in (row for row in rows if row.get('kind') == 'change_update'):
        for change in changes:
            if change.get('item') == update.get('item'):
                change.update({key: update[key] for key in ('platform_result', 'verdicts') if key in update})
    legacy = not changes
    if legacy:
        changes = _legacy_changes(folder, rows)
    terminal = next((row for row in reversed(rows) if row.get('kind') == 'sync_result'), None)
    problems = [problem for row in rows if row.get('kind') == 'check'
                for problem in row.get('problems') or []]
    if terminal is not None:
        passed = bool(terminal.get('ok'))
        problems.extend(terminal.get('problems') or [])
        status = '通過' if passed else '未通過'
    elif problems or any(row.get('platform_result') == '沒做成' for row in changes):
        passed, status = False, '未通過（依既有比對紀錄）'
    elif changes:
        passed, status = None, '無法判定（既有比對未見失敗；舊輪無終局標記）'
    else:
        passed, status = None, '無法判定（沒有逐處比對紀錄）'
    return {'folder': folder, 'identity': _identity(folder, rows), 'changes': changes,
            'passed': passed, 'status': status, 'problems': list(dict.fromkeys(problems))}


def _text(value):
    return html.escape(str(value or ''), quote=True)


def _text_block(label, value):
    full = str(value or '') or '（沒有讀到）'
    short = ' '.join(full.split())
    if len(short) <= 180:
        return f'<div class="text"><b>{_text(label)}</b><pre>{_text(full)}</pre></div>'
    return (f'<details class="text"><summary><b>{_text(label)}</b> '
            f'{_text(short[:177])}…（展開全文）</summary><pre>{_text(full)}</pre></details>')


def _image(folder, rel, label):
    if not rel:
        return f'<div class="missing">{_text(label)}：這輪沒有局部截圖</div>'
    path = os.path.join(folder, os.path.basename(str(rel)))
    if not os.path.isfile(path):
        return f'<div class="missing">{_text(label)}：這輪沒有局部截圖</div>'
    with open(path, 'rb') as source:
        raw = source.read()
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return f'<div class="missing">{_text(label)}：這輪沒有局部截圖</div>'
    data = 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')
    return f'<figure><figcaption>{_text(label)}</figcaption><a href="{data}"><img src="{data}" alt="{_text(label)}"></a></figure>'


def _change_html(folder, row):
    verdicts = row.get('verdicts') or [row.get('verdict') or '未判定']
    verdicts = [str(v) for v in verdicts]
    cls = 'change pending' if PENDING.intersection(verdicts) else 'change'
    result = str(row.get('platform_result') or '沒做成')
    if row.get('reason'):
        result += '（停在' + str(row.get('step') or '未知步驟') + '：' + str(row['reason']) + '）'
    body = [f'<article class="{cls}"><h3>{_text(row.get("item"))}：{_text("、".join(verdicts))}</h3>',
            f'<div class="result"><b>平台實際結果</b><span>{_text(result)}</span></div>',
            _text_block('改前', row.get('before_text')),
            _text_block('改後', row.get('after_text'))]
    if row.get('outside_losses'):
        body.append(_text_block('範圍外消失', '\n'.join(row['outside_losses'])))
    if row.get('answer_question'):
        body.append(f'<p>用到的常用答案：{_text(row["answer_question"])}</p>')
    body.append('<div class="shots">' + _image(folder, row.get('before_shot'), '改前局部截圖')
                + _image(folder, row.get('after_shot'), '改後局部截圖') + '</div></article>')
    return ''.join(body)


def write_report(folders, output=None):
    rounds = [_round(os.path.abspath(folder)) for folder in sorted(folders)]
    groups = defaultdict(list)
    for item in rounds:
        groups[item['identity']].append(item)
    body = ['<!doctype html><html lang="zh-Hant"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            '<title>平台履歷同步驗收報告</title><style>',
            'body{font:16px/1.55 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#17212d;background:#f7f8fa}'
            'h1,h2,h3{line-height:1.3}section,.change{background:#fff;border:1px solid #d6dce3;border-radius:12px;padding:1rem;margin:1rem 0}'
            '.change{border-left:4px solid #536e96}.pending{border-left-color:#b75b36}'
            '.result{display:grid;grid-template-columns:10rem 1fr;gap:1rem;padding:.5rem;background:#f1f5f9;border-radius:6px}'
            '.text{margin:.6rem 0}.text pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;margin:.2rem 0}'
            '.shots{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem;margin-top:1rem}'
            'figure{margin:0}figure img{max-width:100%;max-height:700px;border:1px solid #d6dce3}'
            '.missing{background:#f1f5f9;padding:1rem;border-radius:6px}summary{cursor:pointer;overflow-wrap:anywhere}'
            '.count{display:inline-block;margin:.2rem .4rem .2rem 0;padding:.2rem .5rem;background:#e8edf5;border-radius:999px}'
            '</style><main><h1>平台履歷同步驗收報告</h1>']
    if not groups:
        body.append('<p>這次沒有產生證據輪。</p>')
    for (platform, lang, variant), items in sorted(groups.items()):
        changes = [change for item in items for change in item['changes']]
        counts = Counter(verdict for change in changes
                         for verdict in (change.get('verdicts') or [change.get('verdict') or '未判定']))
        passed = (False if any(item['passed'] is False for item in items)
                  else None if any(item['passed'] is None for item in items) else True)
        body.append(f'<section><h2>{_text(platform)} · {_text(lang)}/{_text(variant)}：'
                    f'{"通過" if passed else "未通過" if passed is False else "無法判定"}</h2>')
        for verdict, count in sorted(counts.items()):
            body.append(f'<span class="count">{_text(verdict)} {count}</span>')
        for item in items:
            body.append(f'<h3>輪次 { _text(os.path.basename(item["folder"])) }：{_text(item["status"])}</h3>')
            if item['problems']:
                body.append('<p>原因：' + _text('；'.join(item['problems'])) + '</p>')
            ordered = sorted(item['changes'], key=lambda row: not bool(PENDING.intersection(row.get('verdicts') or [])))
            body.extend(_change_html(item['folder'], row) for row in ordered)
        body.append('</section>')
    body.append('</main></html>')
    if output is None:
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        output = os.path.join(tempfile.gettempdir(), f'jobsalvo-sync-report-{stamp}.html')
    output = os.path.abspath(output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, 'w', encoding='utf-8') as target:
        target.write(''.join(body))
    return output


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('folders', nargs='+', help='一個或多個 evidence/_sync_profiles/<輪>')
    ap.add_argument('--output', help='產出的本機 HTML 路徑')
    args = ap.parse_args(argv)
    print(write_report(args.folders, args.output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

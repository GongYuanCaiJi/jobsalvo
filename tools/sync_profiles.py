#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把原始履歷這次的改動同步到登記過的每一份平台履歷。"""
import argparse
import contextlib
from collections import Counter
import datetime
import difflib
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent_report           # noqa: E402
import agent_run as ar        # noqa: E402
import apply_run              # noqa: E402
import board_doc as bd        # noqa: E402
import config as cf           # noqa: E402
import evidence               # noqa: E402
import form_record as fr      # noqa: E402
import gate_cells             # noqa: E402
from gate_cells import Problem  # noqa: E402
import profile_sync as ps     # noqa: E402

REPORT_FROM = '平台履歷同步'
TIMEOUT = 40 * 60


slots = ps.slots


def behind(platform, lang, variant, entry, board):
    return ps.state(platform, lang, variant, entry, board) != 'ok'


def pending_count(board, platform=None):
    return sum(behind(plat, lang, variant, entry, board) for plat, lang, variant, entry in slots(platform))


def _out(platform, lang, variant):
    folder = os.path.join(cf.TMP, 'sync-profiles', f'{platform}-{lang}-{variant}'.replace('/', '_'))
    os.makedirs(folder, exist_ok=True)
    return folder


def _norm(value):
    return ' '.join(str(value or '').split())


def _page_text(page):
    """整份閱讀段落接成一段文字(對外頁面核對用)。"""
    reading = (' '.join(page['readingParts']) if isinstance(page.get('readingParts'), list)
               else str(page.get('reading') or ''))
    return _norm(re.sub(r'⟦網址 ([^⟧]*)⟧', r'\1', reading))


def _reading_parts(page):
    parts = page.get('readingParts')
    if isinstance(parts, list) and all(isinstance(part, str) for part in parts):
        return [_norm(part) for part in parts if _norm(part)]
    reading = _page_text(page)
    return [reading] if reading else []


def _selected_parts(parts, ids):
    if (not isinstance(ids, list) or any(type(number) is not int or number < 1 or number > len(parts)
                                         for number in ids) or len(set(ids)) != len(ids)):
        raise ValueError('agent 交回的段落編號無效')
    return [parts[number - 1] for number in ids]


CHECK_MARKS = {'已勾選': '☑', '已選取': '☑', '未勾選': '☐', '未選取': '☐'}


def _plain_part(part):
    """給人看的段落：網址只留網址，勾選狀態寫成 ☑／☐。"""
    part = re.sub(r'^⟦網址 ([^⟧]*)⟧$', r'\1', part)
    return re.sub(r'⟦欄位 (?:(.*) )?(已勾選|未勾選|已選取|未選取)⟧',
                  lambda m: CHECK_MARKS[m.group(2)] + (' ' + m.group(1) if m.group(1) else ''), part)


def _wording_matches(change, parts):
    wanted = _norm(change.get('confirmed_value', change['new']))
    found = _norm(' '.join(_plain_part(part) for part in parts))
    # 只比字:平台把「標題: 說明」拆成兩欄、標點全半形不同,都不算改了寫法
    wanted_words, found_words = re.findall(r'\w+', wanted), re.findall(r'\w+', found)
    words = iter(found_words)
    wording = (''.join(wanted_words) in ''.join(found_words)
               or all(word in words for word in wanted_words)) if wanted else not parts
    # 只比字會放過 markdown 記號殘留(平台上多了 ` 或 **):原稿沒有的記號留在平台上就不是照原文
    if re.search(r'`|\*\*', found) and not re.search(r'`|\*\*', wanted):
        return False
    expected_links = re.findall(r'https?://\S+', wanted) if 'confirmed_value' in change else change.get('links') or []
    actual_links = [part[len('⟦網址 '):-1] for part in parts
                    if part.startswith('⟦網址 ') and part.endswith('⟧')]
    return wording and all(any(unquote(link) in unquote(actual) for actual in actual_links)
                           for link in expected_links)


def _digits_out(text):
    return re.sub(r'\d+', '#', text)


def _outside_losses(before, after, ids=(), ignored=()):
    """找出選取範圍外消失的段落；移位或原段延伸不算消失。"""
    old_parts, new_parts = _reading_parts(before), _reading_parts(after)
    _selected_parts(old_parts, list(ids))
    missing = Counter(old_parts) - Counter(new_parts)
    paired = {}
    for tag, a, b, c, d in difflib.SequenceMatcher(
            None, old_parts, new_parts, autojunk=False).get_opcodes():
        if tag == 'replace':
            paired.update((index, new_parts[c + index - a]) for index in range(a, b)
                          if c + index - a < d)

    outside = []
    for part, count in missing.items():
        if part in ignored:
            continue
        positions = [index for index, value in enumerate(old_parts) if value == part]
        # 原段整段還在新段裡（例如在後面加字），或同一處只有數字在跳（「8分鐘」→「10分鐘」、字數計數），都不算消失。
        if sum(part in paired[index] or _digits_out(part) == _digits_out(paired[index])
               for index in positions if index in paired) >= count:
            continue
        # 同字在多處出現時，無法從字串推斷哪一處被改；要求每個 R0 位置都被選中。
        if any(index + 1 not in ids for index in positions):
            outside.append(part)
    return outside


def _same_page_url(a, b):
    """結尾斜線和 # 之後的部分不影響是不是同一頁。"""
    def key(url):
        parts = urlsplit(str(url or ''))
        return parts.scheme, parts.netloc, parts.path.rstrip('/'), parts.query
    return key(a) == key(b)


def _content_problems(before, after, ids=(), ignored=()):
    return [f'原有文字被改動：{part[:100]}'
            for part in _outside_losses(before, after, ids, ignored)]


def _shown_has(door, url, rnd, parts):
    """對外顯示的頁面(新分頁讀)上,這幾段原文的字都還在。讀不到當作不確定(回 False,照舊報問題)。"""
    import chrome_door
    try:
        page = door.read_shown_page(url) or {}
    except (RuntimeError, chrome_door.NotNow, chrome_door.Unreachable, OSError, ValueError) as error:
        rnd.note('shown_page_failed', reason=str(error))
        return False
    rnd.page(page, 'shown-page', '')
    shown = set(_words(' '.join(_plain_part(p) for p in _reading_parts(page))))
    need = set(_words(' '.join(_plain_part(p) for p in parts)))
    return bool(need) and need <= shown


def _words(text):
    """比內容用的字：英數一串算一個字，中日韓一字算一個。"""
    return re.findall(r'[A-Za-z0-9]+|[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]', str(text or '').lower())


def _unrelated_removals(before, after, ids, related):
    """agent 選進範圍、改後不見的段落，要是這一處的舊稿或新稿；跟兩者都對不上、內容也沒留在改後頁面的，
    是這一處刪掉了別段（例如把整個「關於」欄位換成這一段）。"""
    selected = Counter(_selected_parts(_reading_parts(before), list(ids or [])))
    gone = selected - Counter(_reading_parts(after))
    own = set(_words(related))
    left = Counter(_words(' '.join(_reading_parts(after))))
    unrelated = []
    for part in gone:
        words = _words(_plain_part(part))
        if len(words) < 8:
            continue
        kept = sum((Counter(words) & left).values()) / len(words)
        mine = sum(word in own for word in words) / len(words)
        if kept < 0.8 and mine < 0.5:
            unrelated.append(part)
    return unrelated


def _url(url):
    """比網址用的一種寫法:媒體清單和閱讀段落的網址都解開編碼(%20)再比。"""
    return unquote(str(url or '')).strip()


def _part_urls(parts):
    return [_url(part[len('⟦網址 '):-1]) for part in parts if part.startswith('⟦網址 ') and part.endswith('⟧')]


def _deleted_media(before, after, ids):
    """選進範圍的網址也掛在媒體上(嵌入的影片):改後那個媒體不見,又沒有在這一處換上新的媒體,是刪掉不是換成新值。
    閱讀段落把連結和媒體都寫成網址段落,分不出哪一個是媒體,所以看頁面的媒體清單;
    這一處換上的 = 新出現的媒體裡,網址是這一處新增段落的(範圍外多出來的段落不算)。"""
    selected = set(_part_urls(_selected_parts(_reading_parts(before), list(ids or []))))
    then = Counter(_url(url) for url in before.get('media') or [])
    now = Counter(_url(url) for url in after.get('media') or [])
    here = set(_part_urls(Counter(_added_parts(before, after)) - Counter(_outside_additions(before, after, ids))))
    put = sum(count for url, count in (now - then).items() if url in here)
    return [url for url in (then - now).elements() if url in selected][put:]


def _left_in_place(before, after, ids, new_text, ignored=()):
    """agent 挑了這一處的原文,改後原文一段都沒動、只在旁邊多了段落:舊稿還留在平台上,不是換成新稿。
    原文本來就是新稿的一部分(只在後面補一段)不算。"""
    if not ids or not _added_parts(before, after, ignored):
        return []
    old, new = _reading_parts(before), _reading_parts(after)
    kept = set()
    for tag, a, b, _c, _d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == 'equal':
            kept.update(range(a, b))
    if any(number - 1 not in kept for number in ids):
        return []
    wanted = set(_words(new_text))
    return [old[number - 1] for number in ids if not set(_words(_plain_part(old[number - 1]))) <= wanted]


def _outside_additions(before, after, ids):
    """選取範圍外多出來的段落（例如頁面上的「8分鐘」跳成「10分鐘」）不是這一處的改動。"""
    if not ids:
        return []
    old, new = _reading_parts(before), _reading_parts(after)
    selected = {number - 1 for number in ids}
    outside = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == 'replace' and not selected.intersection(range(a, b)):
            outside.extend(new[c:d])
        elif tag == 'insert' and a not in selected and a - 1 not in selected:
            outside.extend(new[c:d])
    return outside


def _added_parts(before, after, ignored=()):
    """R1 新增的段落；ignored 照次數扣（同一段文字在範圍內外各一份時，只扣範圍外那一份）。"""
    old, new = _reading_parts(before), _reading_parts(after)
    remaining = Counter(new) - Counter(old)
    skip = Counter(ignored)
    added = []
    for part in new:
        if remaining[part]:
            remaining[part] -= 1
            if skip[part]:
                skip[part] -= 1
                continue
            added.append(part)
    return added


def _after_part_ids(before, after, selected_ids, parts):
    """先用 R0 選取位置對齊 R1，再找同字；避免截到另一處的重複文字。"""
    old, new = _reading_parts(before), _reading_parts(after)
    selected = {number - 1 for number in selected_ids}
    nearby = []
    for tag, a, b, c, d in difflib.SequenceMatcher(
            None, old, new, autojunk=False).get_opcodes():
        if tag == 'equal':
            nearby.extend(c + index - a for index in range(a, b) if index in selected)
        elif tag == 'replace' and selected.intersection(range(a, b)):
            nearby.extend(range(c, d))
        elif tag == 'insert' and (not selected or a in selected or a - 1 in selected):
            nearby.extend(range(c, d))
    wanted = Counter(parts)
    found = []
    for index in dict.fromkeys(nearby):
        if wanted[new[index]]:
            found.append(index + 1)
            wanted[new[index]] -= 1
    if wanted:
        for number, part in enumerate(new, 1):
            if number not in found and wanted[part]:
                found.append(number)
                wanted[part] -= 1
    return sorted(found)


def _clip_for_parts(page, ids, padding=12):
    """readingRects 與 readingParts 一對一；回 CDP 文件座標的方框。"""
    rects = page.get('readingRects')
    if not isinstance(rects, list) or len(rects) != len(_reading_parts(page)):
        return None
    boxes = []
    for number in ids:
        if type(number) is not int or not 1 <= number <= len(rects):
            return None
        rect = rects[number - 1]
        if not isinstance(rect, dict):
            continue
        try:
            x, y, width, height = (float(rect[key]) for key in ('x', 'y', 'width', 'height'))
        except (KeyError, TypeError, ValueError):
            continue
        if all(math.isfinite(v) for v in (x, y, width, height)) and width > 0 and height > 0:
            boxes.append((x, y, x + width, y + height))
    if not boxes:
        return None
    size = page.get('documentSize') or {}
    x0 = max(0, math.floor(min(box[0] for box in boxes) - padding))
    y0 = max(0, math.floor(min(box[1] for box in boxes) - padding))
    x1 = math.ceil(max(box[2] for box in boxes) + padding)
    y1 = math.ceil(max(box[3] for box in boxes) + padding)
    if isinstance(size, dict):
        for key, value in (('width', x1), ('height', y1)):
            try:
                limit = float(size.get(key))
                if math.isfinite(limit) and limit > 0:
                    if key == 'width':
                        x1 = min(x1, math.ceil(limit))
                    else:
                        y1 = min(y1, math.ceil(limit))
            except (TypeError, ValueError):
                pass
    return {'x': x0, 'y': y0, 'width': x1 - x0, 'height': y1 - y0} if x1 > x0 and y1 > y0 else None


def _readback_problems(before, after, checked, ids, *, saved, ignored=()):
    """R2 只檢查這一處：撤回有 R0 且沒有本次新增；存檔有本次新增。"""
    baseline = Counter(_reading_parts(before))
    current = Counter(_reading_parts(checked))
    added = Counter(_added_parts(before, after, ignored))
    if saved:
        return [f'存檔後沒有這次新增的文字：{part[:100]}' for part, count in added.items()
                if current[part] < baseline[part] + count]
    selected = Counter(_selected_parts(_reading_parts(before), ids))
    problems = [f'撤回後缺少原文：{part[:100]}' for part, count in selected.items()
                if current[part] < count]
    problems.extend(f'撤回後仍有這次新增的文字：{part[:100]}' for part, count in added.items()
                    if current[part] > baseline[part])
    return problems


def _source_changes(prior, current):
    """上次存妥的版本到現在，按條列或段落給 agent 一次一件事。"""
    old = ps.md_sections(prior) if prior is not None else []
    new = ps.md_sections(current)
    matcher = difflib.SequenceMatcher(None, [(text, tuple(links)) for _where, text, links, _body, _labels in old],
                                       [(text, tuple(links)) for _where, text, links, _body, _labels in new], autojunk=False)
    changes = []
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag == 'equal':
            continue
        for offset in range(max(b-a, d-c)):
            before = old[a+offset] if a+offset < b else ('', '', [], '', [])
            after = new[c+offset] if c+offset < d else ('', '', [], '', [])
            changes.append({'item': after[0] or before[0], 'new': after[1], 'old': before[1],
                            'links': after[2], 'body': after[3], 'link_labels': after[4]})
    return changes


def _answers(board, platform, lang, variant, profile_url=''):
    import form_record as fr
    fb = json.loads(bd.load(board)['fb'])
    shared = [{key: str(answer.get(key) or '') for key in ('k', 'q', 'v', 'zh')}
              for answer in fr.find_shared(fb)]
    return shared + ps.platform_answers(fb, platform, lang, variant, profile_url)


def _confirmed_wording(fb, platform, lang, variant, item, profile_url=''):
    """同一份平台履歷、同一題以使用者最後確認的寫法為準。"""
    matched = [answer for answer in fb.get('__ans__') or [] if isinstance(answer, dict)
               and answer.get('platform') == platform and answer.get('profile') == f'{lang}/{variant}'
               and answer.get('q') == item and answer.get('at') and not answer.get('inf')
               and ps.identity(answer.get('profile_url')) == ps.identity(profile_url)]
    return str(matched[-1].get('v') or '') if matched else None


# 這幾種他做得到:login 登入/驗證、setup 接上瀏覽器 agent/登記平台履歷/補檔、damage 看平台上是不是被改壞。
# 其他(沒有種類的)都是程式或 agent 自己的問題,不列進要你處理:下一次同步或填表前的同步會重試,通過了就自己收掉(_slot_head)
USER_FIXES = {'login', 'setup', 'damage'}


class NeedsHuman(RuntimeError):
    """agent 停在要本人登入、驗證的頁(那一頁已交給他接手)。"""


def _slot_head(platform, lang, variant):
    return f'{platform} {lang}/{variant} 平台履歷同步'


def _failure(platform, lang, variant, entry, board, problems, card_url='', screenshot=''):
    kinds = {gate_cells.kind(p) for p in problems}
    mine = bool(kinds & USER_FIXES)
    need = (('在該頁完成登入/驗證，' if 'login' in kinds else '處理原因後，')
            + ('再按「幫你填表」' if card_url else '再按全部同步')) if mine else ''
    agent_report.report(REPORT_FROM, _slot_head(platform, lang, variant) + '還有：\n' + '\n'.join(problems),
                        need=need, info=not mine, job=card_url, live=board,
                        url=(entry or {}).get('read') or '',
                        ev={'u': card_url or evidence.SYNC_PROFILES_REF, 'f': screenshot} if screenshot else None)
    return False, problems


def _remember_substitute(platform, lang, variant, item, value, board, profile_url='', zh=''):
    """從程式讀到的改後頁面存成該平台專用、待確認的常用答案;英文段落連同他看得懂的翻譯(zh)一起寫。"""
    scope = f'{platform}#{lang}/{variant}#{ps.identity(profile_url)}#{item}'
    base_key = 'p' + hashlib.sha256(scope.encode('utf-8')).hexdigest()[:12]
    key = base_key

    def write(fb):
        nonlocal key
        bank = fb.setdefault('__ans__', [])
        entry = next((answer for answer in bank if answer.get('k') == key), None)
        if entry is not None and entry.get('at'):
            key = base_key + '-' + hashlib.sha256(value.encode('utf-8')).hexdigest()[:8]
            entry = next((answer for answer in bank if answer.get('k') == key), None)
        if entry is None:
            entry = {'k': key, 'q': item, 'platform': platform, 'profile': f'{lang}/{variant}'}
            if profile_url:
                entry['profile_url'] = profile_url
            bank.append(entry)
        if entry.get('v') != value:
            entry.update(v=value, inf=datetime.date.today().isoformat(),
                         why='程式讀到的平台履歷改後內容')
            entry.pop('at', None)
        if zh:
            entry['zh'] = zh
    bd.set_fb(write, live=board, by='sync_profiles')
    return key


def _run_step(step, prompt, out, board, door, rnd, resume=None, bootstrap=''):
    """一步交給 agent 做,接同一段對話(resume)。沒有對話可接、或那一家現在不能用:交給 agent_run 照設定順序挑一個能用的
    瀏覽器 agent 接手同一個工作區(從頭給它 bootstrap)。回 (結果, 紀錄, 交件檔, 換了一隻沒有)。交件與紀錄逐次保留。"""
    import chrome_door
    path, log = os.path.join(out, step + '.json'), os.path.join(out, step + '.log')

    def call(session):
        for stale in (path, log):
            if os.path.isfile(stale):
                os.remove(stale)
        text = prompt if session else bootstrap + prompt
        rnd.instruction(text, agent=door.agent_id if session else None)
        with evidence.activated(rnd):
            return apply_run._run_agent(text, log, cf.HOME, board, timeout=TIMEOUT,
                                        browser_required=True, agent_id=door.agent_id if session else None,
                                        output_last_message=path, report_from=REPORT_FROM,
                                        **({'resume': session} if session else {}))
    outcome = call(resume)
    if resume and not outcome.ok and getattr(outcome, 'status', '') in ('unavailable', 'failed'):
        outcome = call(None)
    rnd.agent_log(log)
    rnd.handoff(path)
    switched = bool(getattr(outcome, 'agent_id', None)) and outcome.agent_id != door.agent_id
    if switched:
        other = chrome_door.of_agent(outcome.agent_id)
        door.agent_id, door.runtime = outcome.agent_id, other.runtime
    return outcome, log, path, switched


def _sync_file(platform, lang, variant, entry, board, *, card_url='', source_file=None,
               effective_file=None, door=None, verify_only=False, round_dirs=None):
    """平台把履歷存成一份檔時，下載真 bytes 比對；相同就不碰網站。"""
    import chrome_door
    source_path = effective_file or source_file or cf.master(variant, lang)
    wanted = ps._sha_file(source_path)
    if not wanted:
        return _failure(platform, lang, variant, entry, board, [Problem('找不到要寄的履歷檔', 'setup')], card_url)
    source = 'sha256:' + wanted
    baseline = entry.get('sync_baseline') or {}
    master_fp = ps._master_fp(lang, variant) if source_file is None else wanted
    if not verify_only and baseline.get('source') == source and not behind(platform, lang, variant, entry, board):
        return True, []
    own_door = door is None
    door = door or chrome_door.current()
    if door is None:
        return _failure(platform, lang, variant, entry, board, [Problem(chrome_door.NO_BROWSER_AGENT, 'setup')], card_url)
    rnd = evidence.Round('sync_profiles', f'{platform}_{lang}_{variant}',
                         cards=[card_url] if card_url else (), board=board)
    if round_dirs is not None:
        round_dirs.append(rnd.dirs[0])
    rnd.note('slot_start', platform=platform, lang=lang, variant=variant,
             changes=1, verify_only=verify_only)
    out = _out(platform, lang, variant)
    registered = ps.where(platform, lang, variant) or {}
    same_entry = ps.identity(registered.get('read')) == ps.identity(entry.get('read'))
    record = {'item': '履歷檔', 'verdicts': ['未判定'], 'platform_result': '沒做成',
              'step': '開啟平台頁面', 'reason': '', 'before_text': '', 'after_text': '',
              'outside_losses': [], 'answer_question': ''}
    slot_ok, slot_problems = False, []

    def fail_file(problems, step):
        slot_problems[:] = problems
        record.update(platform_result='沒做成', step=step, reason='；'.join(problems))
        return _failure(platform, lang, variant, entry, board, problems, card_url)

    try:
        door.open_for_agent(entry.get('edit') or entry['read'])
        record['step'] = '下載平台履歷檔'
        with tempfile.TemporaryDirectory(prefix='jobsalvo-sync-file-') as temp:
            before = door.fetch_file(entry['read'], os.path.join(temp, 'before'))
            before_hash = ps._sha_file(before)
            record['before_text'] = 'SHA-256：' + str(before_hash or '無法讀取')
            record['after_text'] = record['before_text']
            rnd.note('file_before', sha256=before_hash, expected_sha256=wanted)
            if before_hash == wanted:
                rnd.check([], item='履歷檔', already_equal=True)
                if same_entry and not verify_only:
                    ps.remember_sync(platform, lang, variant, source, [], master_fp)
                record.update(verdicts=['檔案 bytes 相同'], platform_result='平台沒變／跟原檔相同', reason='')
                slot_ok = True
                return True, []
            if verify_only:
                problems = ['平台履歷檔與本機檔案 bytes 不同；驗收未存檔']
                rnd.check(problems, item='履歷檔', verification_only=True)
                return fail_file(problems, '驗收檔案 bytes')
            prompt = (door.apply_rule() + '\n' + apply_run.workspace_step(door.workspace) + '\n'
                      + '只在這份平台履歷換成指定檔案並儲存。不要送出應徵、按刪除或發文。\n'
                      + f'檔案：{source_path}\n回傳 JSON：{{"saved":true 或 false,"reason":""}}。')
            outcome, _log, path, _switched = _run_step('file-save', prompt, out, board, door, rnd)
            if not outcome.ok:
                return fail_file(['agent 沒有完成：' + outcome.message()], '儲存履歷檔')
            with open(path, encoding='utf-8') as stream:
                report = json.load(stream)
            after = door.fetch_file(entry['read'], os.path.join(temp, 'after'))
            after_hash = ps._sha_file(after)
            record['after_text'] = 'SHA-256：' + str(after_hash or '無法讀取')
            problems = [] if after_hash == wanted else ['平台履歷檔下載後 bytes 與要寄的檔不同']
            if problems and report.get('reason'):
                problems.append('agent 回報：' + str(report['reason']))
            rnd.note('file_after', sha256=after_hash, expected_sha256=wanted)
            rnd.check(problems, item='履歷檔')
            if problems:
                return fail_file(problems, '下載存檔後履歷檔')
            if ps._sha_file(source_path) != wanted:
                return fail_file(['同步期間原始履歷檔又改了，這份仍落後'], '核對原始履歷檔')
            if same_entry:
                ps.remember_sync(platform, lang, variant, source, [], master_fp)
            record.update(verdicts=['檔案 bytes 相同'], platform_result='已存上平台', reason='')
            slot_ok = True
            return True, []
    except (OSError, ValueError, chrome_door.NotNow, chrome_door.Unreachable) as error:
        return fail_file([str(error)], record['step'])
    finally:
        rnd.note('change', **record)
        save_error = None
        try:
            if same_entry:
                ps.remember_sync_visuals(platform, lang, variant, [dict(record, evidence=rnd.id)])
        except Exception as error:  # noqa: BLE001 — 先寫入失敗證據，再向上回報
            save_error = error
            slot_ok = False
            slot_problems.append('逐處紀錄未存入平台履歷登記：' + str(error))
        rnd.note('sync_result', ok=slot_ok, problems=slot_problems)
        if own_door:
            door.release(None)
        if save_error is not None:
            raise save_error


def sync_slot(platform, lang, variant, entry, board, **kw):
    """同一份平台履歷同一時間只讓一個流程編輯（全部同步、填表前的同步、別的行程）：
    後來的等前一個做完，照登記裡最新的同步基準再看還要不要改。"""
    import fcntl
    page = (entry or {}).get('edit') or (entry or {}).get('read') or f'{platform}#{lang}/{variant}'
    key = hashlib.sha256(str(ps.identity(page) or page).encode('utf-8')).hexdigest()[:16]
    with open(os.path.join(cf.TMP, f'jobsalvo-profile-{key}.lock'), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fresh = ps.where(platform, lang, variant)
        if fresh and entry and ps.identity(fresh.get('read')) == ps.identity(entry.get('read')):
            entry = fresh
        started = datetime.datetime.now().isoformat(timespec='seconds')
        result = _sync_slot(platform, lang, variant, entry, board, **kw)
    if result[0] and not kw.get('verify_only'):
        # 這一份同步通過了:它之前那幾輪的回報講的事都不成立了
        head = _slot_head(platform, lang, variant)
        agent_report.resolve(None, '後來這一份同步通過了', live=board, src=REPORT_FROM, before=started,
                             only=lambda it: str(it.get('msg', '')).startswith(head))
    return result


def _sync_slot(platform, lang, variant, entry, board, *, card_url='', source_file=None, effective_file=None,
               door=None, verify_only=False, round_dirs=None):
    """同步一份。agent 從程式的 R0 段落挑位置；程式比 R0/R1 並讀 R2。"""
    import chrome_door
    if not entry or not entry.get('read'):
        return _failure(platform, lang, variant, entry, board, [Problem('平台履歷未登記', 'setup')], card_url)
    if ps.is_file_url(entry['read']):
        return _sync_file(platform, lang, variant, entry, board, card_url=card_url,
                          source_file=source_file, effective_file=effective_file, door=door,
                          verify_only=verify_only, round_dirs=round_dirs)
    parts = variant.split('+') if source_file is None else [variant]
    try:
        captures = [ps.capture_source(cf.master(part, lang) if source_file is None else source_file)
                    for part in parts]
    except (ValueError, OSError) as error:
        return _failure(platform, lang, variant, entry, board, [str(error)], card_url)
    source = ('\n\n'.join(f'# {part}\n{capture["text"]}' for part, capture in zip(parts, captures))
              if len(parts) > 1 else captures[0]['text'])
    baseline = entry.get('sync_baseline') or {}
    registered = ps.where(platform, lang, variant) or {}
    same_entry = ps.identity(registered.get('read')) == ps.identity(entry.get('read'))
    answer_scope = '' if same_entry else entry['read']
    changes = _source_changes(baseline.get('source'), source)
    current_answers = _answers(board, platform, lang, variant, answer_scope)
    for key, _old_answer, new_answer in ps.changed_answers(baseline, current_answers):
        if any(change['item'] == new_answer['q'] for change in changes):
            continue
        changes.append({'item': new_answer['q'], 'new': new_answer.get('v') or '',
                        'links': re.findall(r'https?://\S+', new_answer.get('v') or ''),
                        'answer_key': key})
    master_fp = '+'.join(capture['sha256'] for capture in captures)
    if verify_only and not changes:
        changes = _source_changes(None, source)
        used_answers = set(baseline['used_answers'] if 'used_answers' in baseline else
                           [answer.get('k') for answer in baseline.get('answers') or []])
        for answer in current_answers:
            if answer.get('k') in used_answers and answer.get('v'):
                if any(change['item'] == answer['q'] for change in changes):
                    continue
                value = answer['v']
                changes.append({'item': answer['q'], 'new': value,
                                'links': re.findall(r'https?://\S+', value),
                                'answer_key': answer['k']})
        if not changes:
            return False, ['沒有可核對的履歷內容，未開網站']
    fb = json.loads(bd.load(board)['fb'])
    for change in changes:
        if change.get('answer_key'):
            continue
        confirmed = _confirmed_wording(fb, platform, lang, variant, change['item'], answer_scope)
        if confirmed is not None:
            change['confirmed_value'] = confirmed
    # 附件也是這一份平台履歷的一部分(本機是真相):要附的檔改過、或從沒核對過,這一輪一起同步
    attachments = ps._attachments_fp(lang, variant) if source_file is None else None
    expected = ps.slot_attachments(lang, variant) if source_file is None else []
    attachments_due = source_file is None and (verify_only or baseline.get('attachments') != attachments)
    if not changes and not attachments_due:
        if same_entry and not verify_only and baseline.get('master') != master_fp:
            ps.remember_sync(platform, lang, variant, source, current_answers, master_fp, attachments=attachments)
        return True, []
    own_door = door is None
    door = door or chrome_door.current()
    if door is None:
        return _failure(platform, lang, variant, entry, board, [Problem(chrome_door.NO_BROWSER_AGENT, 'setup')], card_url)
    out = _out(platform, lang, variant)
    rnd = evidence.Round('sync_profiles', f'{platform}_{lang}_{variant}', cards=[card_url] if card_url else (), board=board)
    if round_dirs is not None:
        round_dirs.append(rnd.dirs[0])
    rnd.note('slot_start', platform=platform, lang=lang, variant=variant,
             changes=len(changes), verify_only=verify_only)
    visuals = []
    try:
        door.open_for_agent(entry.get('edit') or entry['read'])
    except (chrome_door.NotNow, chrome_door.Unreachable) as error:
        if changes:
            row = {'item': changes[0]['item'], 'verdicts': ['未判定'],
                   'platform_result': '沒做成', 'step': '開啟平台頁面', 'reason': str(error),
                   'before_text': '', 'after_text': '', 'outside_losses': [],
                   'answer_question': changes[0]['item'] if changes[0].get('answer_key') else ''}
            rnd.note('change', **row)
            rnd.note('sync_result', ok=False, problems=[str(error)])
            if same_entry:
                ps.remember_sync_visuals(platform, lang, variant, [dict(row, evidence=rnd.id)])
        else:
            rnd.note('sync_result', ok=False, problems=[str(error)])
        return _failure(platform, lang, variant, entry, board, [str(error)], card_url)
    session = None
    tab_id = f'{door.workspace["id"]}:{door.workspace["page"]}'
    substitutions = {}
    pending = []      # 代填的段落:整份做完、翻好才寫進常用答案(答案庫不留沒翻譯的英文答案)
    active_record = None
    active_step = '開啟編輯處'
    slot_ok = False
    slot_problems = []

    def fail(problems, step, shot=''):
        slot_problems[:] = problems
        active_record.update(platform_result='沒做成', step=step, reason='；'.join(problems))
        return _failure(platform, lang, variant, entry, board, problems, card_url, shot)

    def confirm_on_shown_page(rows):
        """編輯頁重新載入看到的可能只是網站草稿；平台實際結果以對外顯示的頁面為準。"""
        if not rows:
            return
        try:
            page = door.read_shown_page(entry['read']) or {}
            rnd.page(page, 'shown-page', '')
            shown = _page_text(page) if _reading_parts(page) else None
            if shown is None:
                rnd.note('shown_page_failed', reason='對外頁面沒有可讀內容')
        except (RuntimeError, chrome_door.NotNow, chrome_door.Unreachable, OSError, ValueError) as error:
            shown = None
            rnd.note('shown_page_failed', reason=str(error))
        for row in rows:
            added = [_page_text({'reading': line}) for line in (row.get('after_text') or '').split('\n')]
            added = [line for line in added if line and not re.fullmatch(r'[\d\s/.,:]+', line)]  # 字數計數等數字不算內容
            if shown is None:
                row['platform_result'] = '無法確認：對外頁面讀不到'
            elif added and all(line in shown for line in added):
                row['platform_result'] = '平台已存（對外頁面看得到）'
            else:
                row['platform_result'] = '平台沒變（編輯頁留有網站草稿）'
                row['verdicts'] = [('編輯頁留有網站草稿' if verdict == '平台自動存檔' else verdict)
                                   for verdict in row.get('verdicts') or []]
            rnd.note('change_update', item=row['item'], platform_result=row['platform_result'],
                     verdicts=row.get('verdicts'))

    def capture_document(page, index):
        """R0 當下整份文件拍一張；agent 交回段落後再裁出改前那一處。拍不到就沒有改前局部圖。"""
        size = page.get('documentSize') or {}
        if not hasattr(door, 'shot_clip') or not page.get('readingRects'):
            return None
        try:
            clip = {'x': 0, 'y': 0, 'width': math.ceil(float(size['width'])),
                    'height': math.ceil(float(size['height']))}
            return door.shot_clip(tab_id, os.path.join(out, f'before-document-{index}.png'), clip)
        except (KeyError, TypeError, ValueError, OSError,
                chrome_door.NotNow, chrome_door.Unreachable) as error:
            rnd.note('clip_missing', phase='before', reason=str(error))
            return None

    def crop_document(document, page, ids, index, record):
        clip = _clip_for_parts(page, ids)
        if not document or not clip:
            return
        try:
            width = int(subprocess.run(['sips', '-g', 'pixelWidth', document], capture_output=True,
                                       text=True, timeout=30).stdout.split()[-1])
            ratio = width / math.ceil(float((page.get('documentSize') or {})['width']))
            target = os.path.join(out, f'before-clip-{index}.png')
            subprocess.run(['sips', '-c', str(round(clip['height'] * ratio)), str(round(clip['width'] * ratio)),
                            '--cropOffset', str(round(clip['y'] * ratio)), str(round(clip['x'] * ratio)),
                            document, '--out', target], capture_output=True, timeout=60, check=True)
            row = rnd.shot(target, f'before-clip-{index}')
            if row.get('file'):
                record['before_shot'] = rnd.rel(row['file'])
                rnd.note('clip', item=record['item'], phase='before', box=clip, shot=row['file'])
        except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError, OSError,
                subprocess.SubprocessError) as error:
            rnd.note('clip_missing', item=record['item'], phase='before', reason=str(error))
        finally:
            with contextlib.suppress(OSError):
                os.remove(document)

    def capture_clip(page, ids, index, phase, record):
        clip = _clip_for_parts(page, ids)
        if not clip or not hasattr(door, 'shot_clip'):
            return
        try:
            shot = door.shot_clip(tab_id, os.path.join(out, f'{phase}-clip-{index}.png'), clip)
            row = rnd.shot(shot, f'{phase}-clip-{index}')
            if row.get('file'):
                record[f'{phase}_shot'] = rnd.rel(row['file'])
                rnd.note('clip', item=record['item'], phase=phase, box=clip, shot=row['file'])
        except (OSError, ValueError, chrome_door.NotNow, chrome_door.Unreachable) as error:
            rnd.note('clip_missing', item=record['item'], phase=phase, reason=str(error))

    def ask(step, goal):
        nonlocal session, tab_id, own_door
        bootstrap = (door.apply_rule() + '\n' + apply_run.workspace_step(door.workspace) + '\n'
                     + '只操作這份平台履歷；不要送出應徵、按刪除或發文。保留工作區。\n')
        prompt = (f'目標：{goal}\n'
                  '交回 JSON：{"tab_id":"工作區編號:頁面標籤","ids":[],"handoff":false,"reason":""}。'
                  '登入或驗證時填 handoff:true，並在 reason 說明。')
        outcome, log, path, switched = _run_step(step, prompt, out, board, door, rnd, session,
                                                  bootstrap=bootstrap)
        if switched:
            session = None
        if not outcome.ok:
            raise RuntimeError('agent 沒有完成：' + outcome.message())
        next_session = ar.session_id(log)
        if not next_session or session and session != next_session:
            raise RuntimeError('接不到同一隻 agent')
        session = next_session
        try:
            with open(path, encoding='utf-8') as stream:
                report = json.load(stream)
        except (OSError, ValueError) as error:
            raise RuntimeError('agent 沒有交回頁面') from error
        if report.get('handoff'):
            read(f'handoff-{index}', visual=False)
            door._hand_off_page()
            own_door = False
            raise NeedsHuman('要你登入/驗證：' + str(report.get('reason') or entry['read']))
        reported_tab = str(report.get('tab_id') or tab_id)
        if reported_tab != tab_id:
            tab_id = door.follow_page(reported_tab)
        return report

    def read(why, *, visual=True, item=''):
        """讀一次並保存同次頁面和截圖。"""
        page = door.read_profile_page(tab_id) or {}
        shot = door.shot(tab_id, os.path.join(out, f'{why}.png'))
        row = rnd.page(page, why, shot)
        if not row.get('shot'):
            raise RuntimeError('頁面截圖失敗')
        if page.get('readyState', 'complete') != 'complete' or not _reading_parts(page):
            raise RuntimeError('讀頁失敗：頁面載入未完成或沒有可讀段落')
        return page

    def translate(pending):
        """代填段落是英文時,請同一隻 agent 翻成他看得懂的語言;每一段都要拿到翻譯。"""
        need = {str(i): value for i, (_, value) in enumerate(pending) if fr.needs_translation(value)}
        if not need:
            return [''] * len(pending)
        prompt = (f'把下面每一段平台履歷文字翻成{fr.lang_words()[0]}，給使用者看懂用；不要操作瀏覽器。\n'
                  + json.dumps(need, ensure_ascii=False)
                  + '\n交回 JSON：{"zh":{"編號":"翻譯"}}，每個編號都要有。')
        outcome, _log, path, _switched = _run_step('translate', prompt, out, board, door, rnd, session)
        if not outcome.ok:
            raise RuntimeError('常用答案翻譯沒有完成：' + outcome.message())
        try:
            with open(path, encoding='utf-8') as stream:
                got = json.load(stream).get('zh') or {}
        except (OSError, ValueError, AttributeError) as error:
            raise RuntimeError('常用答案翻譯沒有交回') from error
        missing = [i for i in need if not str(got.get(i) or '').strip() or fr.needs_translation(got.get(i))]
        if missing:
            raise RuntimeError('常用答案缺翻譯：' + '、'.join(pending[int(i)][0] for i in missing))
        return [str(got.get(str(i)) or '') for i in range(len(pending))]

    def reload_reopen_read(index, phase, before, *, item='', visual=False):
        door.reload_profile_page(tab_id, before.get('url'))
        ask(f'{phase}-reopen-{index}', '回到剛才同一處的編輯頁，停在該處。')
        checked = read(f'{phase}-{index}', visual=visual, item=item)
        if not _same_page_url(checked.get('url'), before.get('url')):
            raise RuntimeError('重新打開後不是同一頁')
        return checked

    def discard(index, before, after, ids, problems, ignored=()):
        try:
            restored = reload_reopen_read(index, 'discard', before)
            rollback_problems = _readback_problems(before, after, restored, ids,
                                                   saved=False, ignored=ignored)
            if rollback_problems and _shown_has(door, entry['read'], rnd,
                                                _selected_parts(_reading_parts(before), list(ids))):
                # 重新打開的編輯處跟改前不是同一個狀態(104 實測:專案名稱和連結那一格沒展開):
                # 平台實際結果以對外顯示的頁面為準,原文都在就是撤回成功
                rnd.note('rollback_confirmed_on_shown_page', item=change['item'], problems=rollback_problems)
                rollback_problems = []
            if rollback_problems:
                problems = [*problems, *rollback_problems, Problem('平台上可能留著改壞的內容', 'damage')]
        except NeedsHuman as error:
            problems = [*problems, Problem(str(error), 'login'), Problem('平台上可能留著改壞的內容', 'damage')]
        except (RuntimeError, chrome_door.NotNow, chrome_door.Unreachable, OSError, ValueError) as error:
            problems = [*problems, '撤回後重讀失敗：' + str(error), Problem('平台上可能留著改壞的內容', 'damage')]
        rnd.check(problems, item=change['item'], phase='discard')
        return fail(problems, '撤回後重讀', rnd.last_shot())

    try:
        for index, change in enumerate(changes):
            answer_question = next((answer.get('q') or change['item'] for answer in current_answers
                                    if answer.get('k') == change.get('answer_key')), change['item']) if change.get('answer_key') else ''
            record = {'item': change['item'], 'verdicts': [], 'platform_result': '沒做成',
                      'step': '開啟編輯處', 'reason': '', 'before_text': '', 'after_text': '',
                      'outside_losses': [], 'answer_question': answer_question}
            active_record = record
            active_step = '開啟編輯處'
            try:
                target = json.dumps({'item': change['item'],
                                     'text': change.get('confirmed_value', change.get('body', change['new'])),
                                     'link_labels': change.get('link_labels') or [],
                                     'links': change['links'] if 'confirmed_value' not in change else
                                              re.findall(r'https?://\S+', change['confirmed_value'])},
                                    ensure_ascii=False)
                where = (f'（上一次你在 {change["edit_page"]} 才完成輸入：直接打開那裡，輸入時不要離開或重新載入這一頁）'
                         if change.get('edit_page') else '')
                ask(f'open-{index}', f'打開原稿這一處的編輯頁{where}，停在待輸入狀態；先別改、別存。原稿：{target}')
                active_step = '讀取改前頁面'
                before = read(f'before-{index}', item=change['item'])
                known_drift = Counter()   # 範圍外的變動，照次數記：同字也在範圍內時不連那一份一起忽略
                r0_tab = tab_id
                parts = _reading_parts(before)
                numbered = '\n'.join(f'{number}. {part}' for number, part in enumerate(parts, 1))
                before_document = capture_document(before, index)
                active_step = '輸入這一處'
                edited = ask(f'edit-{index}',
                             '先挑這一處在 R0 的原文編號，跟原稿相同也要挑；再加入這次會一起變的段落編號'
                             '（例如字數計數）。原文相同（字和標點符號都一樣；換行、拆成不同欄位不算差別）就保持頁面不動；'
                             '平台上多了原稿沒有的符號（例如 ` 或 **）就改掉。沒有原文才選空陣列。'
                             '同一個欄位裡還有別段內容時，只改這一處，其他段落原樣保留。'
                             f'需要修改時放入原稿並停在未存檔狀態。原稿：{target}\nR0 段落：\n{numbered}')
                ids = edited.get('ids')
                try:
                    selected = _selected_parts(parts, ids)
                except ValueError:
                    # 編號不在 R0 上：agent 多半在別頁找到了輸入處。不在 R0 那一頁就同樣到那一頁重讀 R0、重做一次
                    here = door.read_profile_page(tab_id) or {}
                    moved = (not _same_page_url(here.get('url'), before.get('url'))
                             or here.get('documentKey') != before.get('documentKey'))
                    if change.get('edit_page') or not moved:
                        raise
                    rnd.note('moved', item=change['item'], url=here.get('url'), ids=ids)
                    door.reload_profile_page(tab_id, before.get('url'))
                    changes.append(dict(change, edit_page=here.get('url')))
                    record.update(verdicts=['未判定'], platform_result='換到輸入的那一頁重做',
                                  step='輸入這一處', reason=f'agent 在 {here.get("url")} 才找到輸入處')
                    continue
                record['before_text'] = '\n'.join(_plain_part(part) for part in selected)
                record['after_text'] = record['before_text']
                crop_document(before_document, before, ids, index, record)
                active_step = '讀取改後頁面'
                after = read(f'after-{index}', item=change['item'])
                if tab_id != r0_tab:
                    problems = ['agent 沒停在要輸入的那一頁：R0 到 R1 換了分頁']
                    rnd.check(problems, item=change['item'], phase='same_page')
                    return fail(problems, 'R0/R1 同頁檢查', rnd.last_shot())
                reloaded = (before.get('documentKey') is not None and after.get('documentKey') is not None
                            and before['documentKey'] != after['documentKey'])
                if not _same_page_url(after.get('url'), before.get('url')) or reloaded:
                    if not change.get('edit_page'):
                        # R0 讀在錯的頁（agent 輸入時才換到真正的表單）：撤掉沒存的輸入，到那一頁重讀 R0、重做這一處一次
                        door.reload_profile_page(tab_id, before.get('url'))
                        changes.append(dict(change, edit_page=after.get('url')))
                        record.update(verdicts=['未判定'], platform_result='換到輸入的那一頁重做',
                                      step='R0/R1 同頁檢查', reason=f'agent 在 {after.get("url")} 才找到輸入處')
                        continue
                    problems = ([f'agent 沒停在要輸入的那一頁：改前改後不是同一頁：{before.get("url")} → {after.get("url")}']
                                if not _same_page_url(after.get('url'), before.get('url'))
                                else ['agent 沒停在要輸入的那一頁：R0 到 R1 期間重新載入'])
                    rnd.check(problems, item=change['item'], phase='same_page')
                    return fail(problems, 'R0/R1 同頁檢查', rnd.last_shot())

                outside = _outside_losses(before, after, ids, known_drift)
                if outside:
                    # 編輯中這一處以外消失的內容（例如網站浮動按鈕跟著操作收起）不擋，
                    # 連同改前改後截圖列給使用者在確認送出時看。
                    rnd.note('outside_loss_pending', item=change['item'], parts=outside, ids=ids)
                    known_drift.update(outside)
                    record['outside_losses'] = list(outside)
                    record['verdicts'].append('範圍外有內容消失')
                outside_added = _outside_additions(before, after, ids)
                if outside_added:
                    rnd.note('outside_added', item=change['item'], parts=outside_added, ids=ids)
                    known_drift.update(outside_added)
                unexpected = _content_problems(before, after, ids, known_drift) + [
                    f'這一處刪掉了不屬於它的段落：{part[:100]}'
                    for part in _unrelated_removals(before, after, ids, ' '.join(
                        [change.get('old') or '', change.get('new') or '', change.get('confirmed_value') or '']))] + [
                    f'原有媒體被移除或改動：{url}' for url in _deleted_media(before, after, ids)] + [
                    f'原文沒有換掉、只在旁邊加了段落：{part[:100]}' for part in _left_in_place(
                        before, after, ids, change.get('confirmed_value', change['new']), known_drift)]
                rnd.check(unexpected, item=change['item'], phase='compare', ids=ids)
                if unexpected:
                    return discard(index, before, after, ids, unexpected, known_drift)
                stable_before = Counter(_reading_parts(before)) - known_drift
                stable_after = Counter(_reading_parts(after)) - known_drift
                changed = stable_before != stable_after
                added = _added_parts(before, after, known_drift) if changed else []
                # 這一處改後的完整內容 = 新增的段落 + 選取範圍裡沒動的段落，照頁面順序；
                # 只改了其中幾行時，沒動的那幾行也是這一處的內容（顯示、代填答案都用這一份）
                observed = selected
                if changed:
                    remaining = Counter(added) + (Counter(selected) & Counter(_reading_parts(after)))
                    observed = []
                    for part in _reading_parts(after):
                        if remaining[part]:
                            observed.append(part)
                            remaining[part] -= 1
                record['after_text'] = '\n'.join(_plain_part(part) for part in observed)
                capture_clip(after, _after_part_ids(before, after, ids, observed), index, 'after', record)
                actual = '\n'.join(_plain_part(part) for part in observed)
                if _wording_matches(change, observed):
                    actual = None
                rnd.note('verification', item=change['item'],
                         verdict='照原文' if actual is None else 'agent 代填待確認', actual=actual)
                record['verdicts'].insert(0, '照原文' if actual is None else 'agent 代填待確認')
                if not changed:
                    record['platform_result'] = '平台沒變／跟原稿相同' if actual is None else '平台沒變'
                    if actual is not None and not actual.strip():
                        # 沒挑原文也沒放入：這一頁上沒有這一處，agent 也沒放上去。列給他看，不當成代填答案
                        record['verdicts'][0] = '這一頁找不到這一段，沒放上去'
                        continue
                    if not verify_only and actual is not None:
                        pending.append((change['item'], actual))
                    continue
                if verify_only:
                    active_step = '撤回後重新載入'
                    try:
                        restored = reload_reopen_read(index, 'restored', before,
                                                      item=change['item'], visual=True)
                    except NeedsHuman:
                        raise
                    except (RuntimeError, chrome_door.NotNow, chrome_door.Unreachable,
                            OSError, ValueError) as error:
                        problems = [f'驗收撤回後讀頁失敗：{error}；停止驗收，請檢查平台上的內容']
                        rnd.check(problems, item=change['item'], phase='restore_failed',
                                  verification_only=True)
                        return fail(problems, '撤回後重新載入', rnd.last_shot())
                    problems = _readback_problems(before, after, restored, ids,
                                                  saved=False, ignored=known_drift)
                    if problems:
                        added = Counter(_added_parts(before, after, known_drift))
                        current = Counter(_reading_parts(restored))
                        baseline = Counter(_reading_parts(before))
                        kept = [part for part, count in added.items() if current[part] > baseline[part]]
                        if added and len(kept) == len(added):
                            # 平台會自己存檔（例如欄位一離開就存）：這次放入的內容都在，等於已同步。
                            rnd.note('platform_autosaved', item=change['item'])
                            record['verdicts'].append('平台自動存檔')
                            record['platform_result'] = '平台自己存了'
                            problems = []
                        elif not kept:
                            # 放入的都沒留下；這一處和改前不同（例如網站自己的草稿被清掉）就列給使用者看。
                            rnd.note('restored_differs_pending', item=change['item'], problems=problems)
                            record['verdicts'].append('重新載入後與改前不同')
                            record['platform_result'] = '只驗收、已撤回'
                            problems = []
                    rnd.check(problems, item=change['item'], phase='restored_persisted',
                              verification_only=True)
                    if problems:
                        return fail(problems, '撤回後比對', rnd.last_shot())
                    record['platform_result'] = record['platform_result'] if record['platform_result'] != '沒做成' else '只驗收、已撤回'
                    continue
                active_step = '儲存這一處'
                ask(f'save-{index}', '儲存這一處。')
                active_step = '存檔後重新載入'
                try:
                    saved = reload_reopen_read(index, 'saved', before)
                except NeedsHuman:
                    raise
                except (RuntimeError, chrome_door.NotNow, chrome_door.Unreachable,
                        OSError, ValueError) as error:
                    return fail(['存檔後重讀不到改後內容：' + str(error)], '存檔後重新載入', rnd.last_shot())
                problems = _readback_problems(before, after, saved, ids,
                                              saved=True, ignored=known_drift)
                rnd.check(problems, item=change['item'], phase='saved')
                if problems:
                    return fail(problems, '存檔後比對', rnd.last_shot())
                record['platform_result'] = '已存上平台'
                if actual is not None:
                    pending.append((change['item'], actual))
                continue
            except (RuntimeError, chrome_door.NotNow, chrome_door.Unreachable, OSError, ValueError) as error:
                if record['platform_result'] == '沒做成':
                    record.update(step=active_step, reason=str(error))
                raise
            finally:
                if not record['verdicts']:
                    record['verdicts'] = ['未判定']
                rnd.note('change', **record)
                visuals.append(dict(record, evidence=rnd.id))
        if attachments_due:
            index, active_step = 'attachments', '核對平台附件'
            with tempfile.TemporaryDirectory(prefix='jobsalvo-sync-attachments-') as folder:
                problems = ps.downloaded_problems(door.download_attachments, entry['read'],
                                                  os.path.join(folder, 'before'), expected)
                if problems and not verify_only:
                    ask('attachments', '把這份平台履歷存的檔改成剛好是下面這幾份並存檔:同名的換成這一份、少的上傳、'
                        '其他的刪掉。只動檔案。\n' + '\n'.join(f'- {name}:{path}' for name, path in expected))
                    problems = ps.downloaded_problems(door.download_attachments, entry['read'],
                                                      os.path.join(folder, 'after'), expected)
            rnd.check(problems, item='附件', verification_only=verify_only)
            if problems:
                slot_problems[:] = problems
                return _failure(platform, lang, variant, entry, board, problems, card_url)
        if verify_only:
            confirm_on_shown_page([row for row in visuals if row.get('platform_result') == '平台自己存了'])
            slot_ok = True
            return True, []
        active_step = '常用答案補翻譯'
        for (item, value), zh in zip(pending, translate(pending)):
            substitutions[_remember_substitute(platform, lang, variant, item, value, board,
                                               answer_scope, zh=zh)] = value
        final_answers = _answers(board, platform, lang, variant, answer_scope)
        initial = {answer.get('k'): answer for answer in current_answers if answer.get('k')}
        final = {answer.get('k'): answer for answer in final_answers if answer.get('k')}
        if any(initial[key] != final.get(key) for key in initial if key not in substitutions) or any(
                (final.get(key) or {}).get('v') != value for key, value in substitutions.items()):
            slot_problems[:] = ['同步期間常用答案又改了，這份仍落後']
            return _failure(platform, lang, variant, entry, board, slot_problems, card_url)
        if (ps._master_fp(lang, variant) if source_file is None else ps._sha_file(source_file)) != master_fp:
            slot_problems[:] = ['同步期間原始履歷又改了，這份仍落後']
            return _failure(platform, lang, variant, entry, board, slot_problems, card_url)
        # 用在這一份的常用答案:以前就用的、這一輪改到的、值出現在這一輪程式核對過的這一處原文(改前、改後)裡的
        before_used = set(baseline.get('used_answers') or [])
        touched = [row[key] for row in visuals for key in ('before_text', 'after_text')]
        used = [answer['k'] for answer in final_answers if answer.get('k') and (
            answer['k'] in before_used or any(change.get('answer_key') == answer['k'] for change in changes)
            or answer.get('v') and any(_norm(answer['v']) in _norm(text) for text in touched))]
        if same_entry:
            ps.remember_sync(platform, lang, variant, source, final_answers, master_fp, used_answers=used,
                             attachments=attachments)
        slot_ok = True
        return True, []
    except (RuntimeError, chrome_door.NotNow, chrome_door.Unreachable, OSError, ValueError) as error:
        slot_problems[:] = [Problem(str(error), 'login') if isinstance(error, NeedsHuman) else str(error)]
        return _failure(platform, lang, variant, entry, board, list(slot_problems), card_url,
                        rnd.last_shot() if verify_only else '')
    finally:
        save_error = None
        try:
            if same_entry and visuals:
                ps.remember_sync_visuals(platform, lang, variant, visuals)
        except Exception as error:  # noqa: BLE001 — 先寫入失敗證據，再向上回報
            save_error = error
            slot_ok = False
            slot_problems.append('逐處紀錄未存入平台履歷登記：' + str(error))
        rnd.note('sync_result', ok=slot_ok, problems=slot_problems)
        if own_door:
            try:
                door.release(None)
            except Exception as error:  # noqa: BLE001 — 單份出錯要寫進進度，其餘平台仍要完成
                print(f'{platform} {lang}/{variant} 工作區沒收掉：{error}', file=sys.stderr)
        if save_error is not None:
            raise save_error


PARALLEL = 4  # ponytail: 依記憶體定的上限；每份一隻 agent 加一個 ego 分頁


def sync_all(board, platform=None, progress=None, *, verify_only=False, round_dirs=None):
    """不同頁面的平台履歷同時跑（最多 PARALLEL 份）；同一頁的幾份一次一份。"""
    by_page = {}
    for slot in slots(platform):
        if verify_only or behind(*slot, board):
            entry = slot[3] or {}
            by_page.setdefault(ps.identity(entry.get('edit') or entry.get('read')) or slot[:3], []).append(slot)
    results, lock = {}, threading.Lock()
    room = threading.Semaphore(PARALLEL)

    def one(group):
        with room:
            run_group(group)

    def run_group(group):
        for plat, lang, variant, entry in group:
            try:
                result = sync_slot(plat, lang, variant, entry, board,
                                   **({'verify_only': True} if verify_only else {}),
                                   **({'round_dirs': round_dirs} if round_dirs is not None else {}))
            except Exception as error:  # noqa: BLE001 — 收工作區失敗只記錯，不蓋掉同步結果
                result = _failure(plat, lang, variant, entry, board,
                                  [f'{type(error).__name__}: {str(error)[:200]}'])
            with lock:
                results[(plat, lang, variant)] = result
                if progress:
                    progress(results)
                print(f'{plat} {lang}/{variant}:' + ('驗收通過' if verify_only and result[0] else
                      '通過' if result[0] else '還有 ' + '; '.join(result[1])[:300]),
                      flush=True)
            if verify_only and not result[0]:
                break
    threads = [threading.Thread(target=one, args=(group,)) for group in by_page.values()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def write_status(path, started, total, needed, results=None, finished=False):
    """設定頁的背景進度與每份結果。"""
    if not path:
        return
    import jobrun
    results = results or {}
    count = len(results)
    passed = sum(1 for ok, _problems in results.values() if ok)
    if not needed:
        phase, message = 'nothing', '沒有要同步的'
    elif finished:
        phase, message = ('done' if passed == total else 'incomplete'), f'同步完成：{passed}/{total} 份通過'
    else:
        phase, message = 'agent', f'已處理 {count}/{total} 份' if count else '同步中'
    row = {'phase': phase, 'pid': os.getpid(), 't0': started, 'n': total,
           'done': count, 'msg': message,
           'results': [{'platform': p, 'lang': l, 'variant': v, 'ok': result[0],
                        'msg': '; '.join(result[1])[:200]} for (p, l, v), result in results.items()]}
    if finished:
        row['finished_at'] = time.time()
    jobrun.write(path, row)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--platform')
    ap.add_argument('--board', default=bd.LIVE)
    ap.add_argument('--verify-only', action='store_true', help='驗收所有登記的平台履歷；改後比對並撤回暫存修改，不存檔或更新同步基準')
    args = ap.parse_args()
    board = os.path.abspath(args.board)
    status_dir = os.environ.get('SYNC_TMP')
    needed = len(slots(args.platform)) if args.verify_only else pending_count(board, args.platform)
    total = needed
    started = time.time()
    status_path = os.path.join(status_dir, 'sync_status.json') if status_dir else None
    if status_path:
        os.makedirs(status_dir, exist_ok=True)
    write_status(status_path, started, total, needed)
    round_dirs = [] if args.verify_only else None
    results = sync_all(board, args.platform,
                       lambda rows: write_status(status_path, started, total, needed, rows),
                       verify_only=args.verify_only, round_dirs=round_dirs)
    ok = sum(1 for result in results.values() if result[0])
    write_status(status_path, started, total, needed, results, finished=True)
    print(f'{"驗收完成（未存檔）" if args.verify_only else "同步完成"}:{ok}/{len(results)} 份通過')
    if args.verify_only:
        import sync_report
        print('驗收報告：' + sync_report.write_report(round_dirs))
    return 0 if ok == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

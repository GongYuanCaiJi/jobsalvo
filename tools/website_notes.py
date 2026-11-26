"""
website_notes —— 按網址主機保存網站筆記;使用者寫或改過的那幾條交給後續網站任務的 agent(users_words)。

- for_url(s) 只按主機名取筆記，不合併不同子網域。
- remember() 按 agent 回報網址歸類筆記，每個主機最多留 12 則 agent 筆記。
- 新檔首次讀取時遷移舊平台筆記；user_action() 回傳有條件的復原資料。
"""
import datetime
import json
import os
import uuid
from contextlib import contextmanager

import config as cf

NOTES_MAX = 12


def host_key(url):
    value = str(url or '')
    if value.split(':', 1)[0].lower() not in ('http', 'https'):
        return ''
    from web.seen import host as page_host_key
    try:
        return page_host_key(value)
    except ValueError:
        return ''


hostname = host_key


def _path():
    return cf.path('website-notes.json')


@contextmanager
def _notes_lock(path=None):
    import board_doc
    with board_doc.live_lock(path or _path()):
        yield


def _migrate(path):
    try:
        with open(cf.path('platform-notes.json'), encoding='utf-8') as f:
            old = json.load(f)
    except (OSError, ValueError):
        return {'schema_version': 1, 'hosts': {}}
    if not isinstance(old, dict):
        return {'schema_version': 1, 'hosts': {}}
    hosts = {}
    import platform_hosts
    legacy_platforms = platform_hosts.defaults()
    for key, values in old.items():
        key = str(key)
        if key.startswith('_') or not isinstance(values, list):
            continue
        # 舊檔的鍵是舊版程式用的平台名(就是出廠預設裡的平台)或主機名。升級後行為照舊:
        # 平台名的筆記放在舊版放的那個主機(出廠預設那個平台的第一個主機,跟舊版平台表同一份資料);
        # 其他鍵本身就是主機。使用者自己編輯的清單不影響搬到哪裡。
        host = (legacy_platforms.get(key.casefold()) or [key])[0]
        _legacy_rows(hosts.setdefault(host, []), host, values)
    data = {'schema_version': 1, 'hosts': hosts}
    _write(data, path)
    return data


def _legacy_rows(rows, host, values):
    for value in values:
        rows.append({
            'id': uuid.uuid4().hex,
            'host': host,
            'text': str(value),
            'url': None,
            'date': None,
            'card_id': None,
            'round_id': None,
            'author': 'agent',
            'legacy': True,
        })


def _read_unlocked(path=None):
    path = path or _path()
    if not os.path.exists(path):
        return _migrate(path)
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get('hosts'), dict):
        return {'schema_version': 1, 'hosts': {}}
    return data


def _read(path=None):
    with _notes_lock(path):
        return _read_unlocked(path)


def _write(data, path=None):
    path = path or _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _matches_host(host, note_host):
    return host == str(note_host).rstrip('.').casefold()


def for_urls(urls):
    hosts = {hostname(url) for url in urls}
    hosts.discard('')
    stored = _read()['hosts']
    return {
        host: [
            dict(note)
            for note_host, rows in stored.items()
            for note in rows
            if isinstance(note, dict)
            and _matches_host(host, note_host)
        ]
        for host in hosts
    }


def for_url(url):
    host = hostname(url)
    return for_urls([url]).get(host, []) if host else []


def users_words(urls=None):
    """給 agent 的網站筆記:只有使用者自己寫或改過的那幾條原文,照主機分(#831);沒給網址就是全部主機。
    agent 寫的筆記只給使用者看,不再交給下一個 agent。"""
    wanted = None if urls is None else {hostname(url) for url in urls} - {''}
    words = {}
    for note_host, rows in _read()['hosts'].items():
        host = str(note_host).rstrip('.').casefold()
        if wanted is None or host in wanted:
            for note in rows if isinstance(rows, list) else ():
                if isinstance(note, dict) and note.get('author') == 'user' and note.get('text'):
                    words.setdefault(host, []).append(str(note['text']))
    return words


def all_notes():
    notes = []
    for host, rows in _read()['hosts'].items():
        if isinstance(rows, list):
            notes.extend(dict(row) for row in rows if isinstance(row, dict))
    return sorted(notes, key=lambda row: (row.get('host') or '', row.get('date') or '', row.get('id') or ''))


def _clean_text(text):
    return ' '.join(str(text or '').split())[:200]


def user_action(action, url=None, text=None, note_id=None, undo=None):
    """跑一個使用者筆記動作，回傳只在目標筆記沒再變動時可用的復原單。"""
    with _notes_lock():
        data = _read_unlocked()
        hosts = data['hosts']
        if action == 'undo':
            return _undo_user_action(data, undo)
        if action == 'add':
            host = hostname(url)
            clean = _clean_text(text)
            if not host or not clean:
                raise ValueError('請填有效網址和筆記')
            note = {
                'id': uuid.uuid4().hex,
                'host': host,
                'text': clean,
                'url': str(url).strip(),
                'date': datetime.date.today().isoformat(),
                'card_id': None,
                'round_id': None,
                'author': 'user',
            }
            rows = hosts.setdefault(host, [])
            rows.append(note)
            index = len(rows) - 1
            before = None
        elif action == 'edit':
            clean = _clean_text(text)
            if not clean:
                raise ValueError('筆記不能空白')
            found = _find_note(hosts, note_id)
            if not found:
                return None
            host, index, current = found
            before = dict(current)
            current['text'] = clean
            current['date'] = datetime.date.today().isoformat()
            current['author'] = 'user'
            note = dict(current)
        elif action == 'delete':
            found = _find_note(hosts, note_id)
            if not found:
                return None
            host, index, current = found
            before = dict(current)
            note = None
            rows = hosts[host]
            del rows[index]
            if not rows:
                del hosts[host]
        else:
            return None

        _write(data)
        return {'note': note, 'undo': {'kind': 'website_note', 'id': note['id'] if note else note_id,
                                       'before': before, 'after': dict(note) if note else None,
                                       'index': index}}


def _find_note(hosts, note_id):
    for host, notes in hosts.items():
        if isinstance(notes, list):
            for index, note in enumerate(notes):
                if isinstance(note, dict) and note.get('id') == note_id:
                    return host, index, note
    return None


def _undo_user_action(data, slip):
    if not isinstance(slip, dict) or slip.get('kind') != 'website_note':
        return False
    hosts = data['hosts']
    note_id = slip.get('id')
    found = _find_note(hosts, note_id)
    before, after = slip.get('before'), slip.get('after')
    if after is None:
        if found:
            return False
        if not isinstance(before, dict) or before.get('id') != note_id:
            return False
        index = slip.get('index', 0)
        if not isinstance(index, int) or isinstance(index, bool):
            return False
        host = str(before.get('host') or '')
        if not host:
            return False
        rows = hosts.setdefault(host, [])
        rows.insert(min(max(index, 0), len(rows)), dict(before))
    elif before is None:
        if not found or found[2] != after:
            return False
        host, index, _ = found
        del hosts[host][index]
        if not hosts[host]:
            del hosts[host]
    else:
        if not found or found[2] != after:
            return False
        host, index, _ = found
        hosts[host][index] = dict(before)
    _write(data)
    return True


def remember(add, card_id=None, round_id=None):
    """收 agent 這一輪寫的筆記;agent 看不到別人寫的筆記,所以也不刪筆記(#831),過時的由使用者在看板刪。"""
    with _notes_lock():
        data = _read_unlocked()
        hosts = data['hosts']
        changed = False
        for item in add if isinstance(add, list) else []:
            if not isinstance(item, dict):
                continue
            note_host = hostname(item.get('url'))
            text = ' '.join(str(item.get('note') or '').split())[:200]
            if not note_host or not text:
                continue
            notes = hosts.setdefault(note_host, [])
            if any(' '.join(str(n.get('text') or '').split()) == text for n in notes if isinstance(n, dict)):
                continue
            notes.append({
                'id': uuid.uuid4().hex,
                'host': note_host,
                'text': text,
                'url': str(item['url']).strip(),
                'date': datetime.date.today().isoformat(),
                'card_id': card_id,
                'round_id': round_id,
                'author': 'agent',
            })
            changed = True

        for host, notes in list(hosts.items()):
            if not isinstance(notes, list):
                continue
            agent_indexes = [i for i, note in enumerate(notes)
                             if isinstance(note, dict) and note.get('author') != 'user']
            excess = len(agent_indexes) - NOTES_MAX
            if excess > 0:
                old = set(agent_indexes[:excess])
                hosts[host] = [note for i, note in enumerate(notes) if i not in old]
                changed = True

        if changed:
            data['schema_version'] = 1
            _write(data)

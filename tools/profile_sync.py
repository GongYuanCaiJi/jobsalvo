#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台履歷登記、同步基準與專用答案。"""
import os, sys, re, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cf
import profile_registry
import sync_material
import sync_records


def _host(url):
    from web.seen import host as host_key
    try:
        return host_key(str(url or '').strip())
    except ValueError:
        return ''


def _listed_host(value):
    """清單上的一格:主機名或整個網址都收。"""
    value = str(value or '').strip()
    return _host(value if '//' in value else f'//{value}')


# 每個平台涵蓋哪些主機(子網域也算)是一份可編輯的清單,存在登記表那個平台的 hosts。
# 出廠預設放在 defaults/platform_hosts.json(資料,不是程式規則):登記表裡那個平台還沒有 hosts 時用它,
# 所以舊版本認得的平台升級後照舊認得,使用者不用補;使用者一改(set_hosts)就以他的為準。
DEFAULT_HOSTS_FILE = os.path.join(os.path.dirname(HERE), 'defaults', 'platform_hosts.json')


def default_hosts():
    """出廠預設的平台主機清單:{平台: [主機, …]}。"""
    import platform_hosts
    return platform_hosts.defaults()


def _covered(host, hosts):
    return any(host == listed or host.endswith('.' + listed) for listed in hosts)


def platform_hosts(platform, reg=None):
    """這個平台涵蓋的主機,依序:登記表 hosts 清單(沒有這份清單時用出廠預設)、平台鍵本身
    (沒登記平台名時鍵就是主機名)、他登記在這個平台下的網址的主機。"""
    reg = registry() if reg is None else reg
    entries = reg.get(platform)
    entries = entries if isinstance(entries, dict) else {}
    if 'hosts' in entries:
        listed = [_listed_host(host) for host in entries.get('hosts') or [] if isinstance(host, str)]
    else:
        listed = default_hosts().get(platform, [])
    urls = [entry.get(key) for entry in entries.values() if isinstance(entry, dict) and entry.get('by') != 'agent'
            for key in ('read', 'edit')]
    urls.append((reg.get('_application_records') or {}).get(platform))
    hosts = [*listed, _listed_host(platform), *map(_host, urls)]
    return list(dict.fromkeys(host for host in hosts if host))


def platform_of(url):
    """網址屬於哪個登記過的平台:主機等於某平台的主機或是它的子網域。最長的主機勝出;
    同樣長的主機對到兩個平台就不猜,回 None。"""
    from web.seen import platform_of as classify
    reg = registry()
    hosts = {platform: platform_hosts(platform, reg)
             for platform in dict.fromkeys([*reg, *default_hosts()])
             if not str(platform).startswith('_')}
    return classify(str(url or ''), hosts)


def profile_key(url):
    """申請頁或平台履歷網址屬於哪個平台:登記過的平台優先,不然用網址的主機名;是否比對由 delivery.method 決定。"""
    from web.seen import host as host_key
    return platform_of(url) or host_key(str(url or ''))


def _load_registry():
    """從檔案讀一份新的;要改登記表的(_registry_writer 裡)用這個,改的是自己那一份。"""
    return profile_registry.load()


_REG_CACHE = (None, None)


def stamp():
    """登記表現在是哪一份:換檔(inode)、內容被改(修改時間、狀態變更時間、大小)都會變;沒有這個檔回 None。"""
    try:
        st = os.stat(profile_registry.path())
    except FileNotFoundError:
        return None
    return (profile_registry.path(), st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)


def registry():
    """只讀用:檔案沒換過就用上次解析好的那一份,回傳的不能改(#607)。
    登記表有幾十 MB,看板每算一次卡片下一步就要讀好幾次。"""
    global _REG_CACHE
    key = stamp()
    if key is None:
        return {}
    cached_key, reg = _REG_CACHE
    if cached_key != key:
        reg = _load_registry()
        _REG_CACHE = (key, reg)
    return reg


def _registry_writer(method):
    """共用既有檔案鎖，保護每個完整的讀→改→寫。"""
    from functools import wraps

    @wraps(method)
    def write(*args, **kwargs):
        import board_doc
        os.makedirs(os.path.dirname(profile_registry.path()) or '.', exist_ok=True)
        with board_doc.live_lock(profile_registry.path()):
            return method(*args, **kwargs)
    return write


def _save_registry(reg):
    profile_registry.save(reg)


@_registry_writer
def set_hosts(platform, hosts):
    """使用者編輯一個平台涵蓋哪些主機(可編輯清單,存在登記表);新平台照同一條路登記。"""
    platform = str(platform or '').strip()
    if not platform or platform.startswith('_') or '/' in platform:
        raise ValueError('平台名稱不能空白、不能以 _ 開頭、不能有 /')
    listed = [_listed_host(host) for host in hosts]
    if not all(listed):
        raise ValueError('有主機名稱讀不出來')
    reg = _load_registry()
    entries = reg.setdefault(platform, {})
    entries['hosts'] = list(dict.fromkeys(listed))
    _save_registry(reg)
    return entries['hosts']


@_registry_writer
def remember(platform, lang, variant, read_url, edit_url=None, by=None, name=None, only_new=False):
    """記下那一份平台履歷在哪。by='agent' 是 agent 登記的:看板標明,它的主機不算進這個平台(S-22);
    使用者自己登記的,那個主機算這個平台(S-23)。only_new:那一份已經登記過就不動(查與寫在同一把鎖裡,同時兩張先登記的算)。"""
    reg = _load_registry()
    entry = dict((reg.get(platform) or {}).get(f'{lang}/{variant}') or {})
    if only_new and entry:
        return False
    if not entry:
        entry['kind'] = 'page'
    entry.update(read=read_url, edit=edit_url or read_url)
    entry.pop('by', None)
    entry.update({'by': by} if by else {}, **({'name': name} if name else {}))
    entries = reg.setdefault(platform, {})
    if 'hosts' not in entries:
        # 新登記的平台:清單先帶入出廠預設,再補上登記網址的主機(已涵蓋的不重複列)
        hosts = list(default_hosts().get(platform, []))
        for url in (read_url, edit_url):
            host = _host(url)
            if host and not _covered(host, hosts):
                hosts.append(host)
        entries['hosts'] = hosts
    entries[f'{lang}/{variant}'] = entry
    _save_registry(reg)


def where(platform, lang, variant):
    return (registry().get(platform) or {}).get(f'{lang}/{variant}')


def _master_fp(lang, variant):
    import source_sync
    paths = [cf.master(part, lang) for part in variant.split('+')]
    return '+'.join(source_sync.fingerprint(p) for p in paths) if all(paths) else ''


def sync_version(entry):
    """最新一版改動清單的證據輪次；舊登記沿用最後一列的輪次。"""
    return sync_records.version(entry)


def sync_notice_for_url(fb, url, job):
    """卡片使用落後或待核對的履歷時顯示提示,但不阻擋投遞。"""
    delivery = ((fb.get(url) or {}).get('apply') or {}).get('delivery') or {}
    if delivery.get('method') != 'platform_profile' or not delivery.get('profile_url'):
        return None
    try:
        decision = decided(job, fb, url)
        if decision.get('profile_kind') != 'fixed':
            return None
        selected = (decision.get('platform'), decision.get('lang'), decision.get('variant'))
        profiles = slots()
    except (OSError, KeyError, TypeError, ValueError):
        return '讀不到平台履歷同步狀態，請自行確認'
    for platform, lang, variant, entry in profiles:
        if (platform, lang, variant) != selected:
            continue
        if entry.get('read') != delivery['profile_url']:
            return '讀不到平台履歷同步狀態，請自行確認'
        notices = []
        try:
            version = sync_version(entry)
            versions = entry.get('sync_versions') or []
            latest = versions[-1] if versions and isinstance(versions[-1], dict) else {}
            if version and not latest.get('reviewed_at'):
                notices.append('平台履歷最新同步清單尚未看過')
            current = state(platform, lang, variant, entry)
        except (OSError, KeyError, TypeError, ValueError):
            notices.append('讀不到平台履歷同步狀態，請自行確認')
            return '；'.join(notices)
        if current == 'master-changed':
            notices.append('平台履歷落後')
        elif current == 'unchecked':
            notices.append('平台履歷還沒同步')
        return '；'.join(notices) or None
    return None


def mark_sync_reviewed(platform, lang, variant, version):
    """只記下畫面仍顯示的那一版；同步剛產生新清單時拒絕舊版本。"""
    return sync_records.mark_reviewed(platform, lang, variant, version)


def slots(platform=None):
    """登記過的每一份平台履歷:[(平台, 語言, 履歷, 那一筆)]。"""
    return [(p, key.partition('/')[0], key.partition('/')[2], entry) for p, entries in registry().items()
            if not str(p).startswith('_') and isinstance(entries, dict) and (not platform or p == platform)
            for key, entry in entries.items() if '/' in key and isinstance(entry, dict) and entry.get('read')]


def state(platform, lang, variant, entry, board=None):
    """這一份平台履歷跟本機的關係:ok(跟上) / master-changed(落後或最新清單還沒看過) / unchecked(還沒同步)。"""
    import sync
    rows = sync_records.versions_for(platform, lang, variant)      # 舊登記升級轉換後的版本;呼叫的人接著從 entry 讀版本
    entry.update({'sync_versions': rows} if rows else {})
    code = sync.standing(sync.slot_of(platform, lang, variant, entry), board).code
    return {'STANDING_NEVER': 'unchecked', 'STANDING_OK': 'ok'}.get(code, 'master-changed')


def _confirmation_pending(fb, platform, lang, variant, entry):
    """答案待確認狀態由答案庫提供;同步版本不另存一份答案狀態。"""
    import form_record_state
    profile = f'{lang}/{variant}'
    answers = {answer.get('k') for answer in fb.get('__ans__') or []
               if isinstance(answer, dict) and answer.get('platform') == platform
               and answer.get('profile') == profile and answer.get('k')}
    waiting = {answer.get('k') for answer, _users in form_record_state.waiting(fb)}
    return bool(answers & waiting)


def status(board=None):
    """登記過、有原始履歷的每一份平台履歷的狀態(看板設定頁「全部同步」那一區),以及是否仍有代填內容等使用者確認。"""
    shared, board_error = None, ''
    if board:
        try:
            shared = sync_material.shared_answers(board)
        except (OSError, ValueError) as error:       # 設定頁讀不到看板也要開得起來,並說看板讀不到
            board_error = f'看板讀不到,不知道有沒有代填內容等你確認:{error}'
    out = []
    for p, lang, v, entry in slots():
        if not _master_fp(lang, v):
            continue
        st = state(p, lang, v, entry, board)
        version = sync_version(entry)
        kept = [row for row in entry.get('sync_versions') or [] if isinstance(row, dict)]
        visuals = []
        if version:
            for row in entry.get('sync_visuals') or []:
                if isinstance(row, dict) and row.get('evidence') == version:
                    visible = dict(row)
                    visible.pop('page_snapshots', None)
                    if not isinstance(visible.get('pages_compared'), list) or (
                            not visible.get('pages_compared')
                            and (visible.get('before_pages') or visible.get('after_pages'))):
                        before, after = visible.get('before_pages') or {}, visible.get('after_pages') or {}
                        pages = []
                        for tab_id in dict.fromkeys([*before, *after]):
                            old = (before.get(tab_id) or {}).get('page') or {}
                            new = (after.get(tab_id) or {}).get('page') or {}
                            old_url, new_url = str(old.get('url') or ''), str(new.get('url') or '')
                            pages.append({'page_tab_id': str(tab_id), 'page_url': new_url or old_url,
                                          'before_url': old_url, 'after_url': new_url})
                        visible['pages_compared'] = pages
                    for key in ('before_pages', 'after_pages', 'saved_pages', 'restored_pages', 'persisted_pages'):
                        visible.pop(key, None)
                    visuals.append(visible)
        out.append({'platform': p, 'lang': lang, 'variant': v, 'state': st, 'url': entry['read'],
                    'board_error': board_error,
                    'confirmation_pending': bool(st == 'ok' and shared
                                                 and _confirmation_pending(shared[0], p, lang, v, entry)),
                    'sync_review': {'version': version,
                                    'reviewed': bool(version and kept and kept[-1].get('reviewed_at'))},
                    'sync_visuals': visuals})
    return out


@_registry_writer
def remember_name(platform, lang, variant, name):
    """平台上那一份叫什麼(申請頁選平台履歷時顯示的字):程式在那一份的頁面上看到過才記。"""
    reg = _load_registry()
    slot = (reg.get(platform) or {}).get(f'{lang}/{variant}')
    if isinstance(slot, dict) and slot.get('name') != name:
        slot['name'] = name
        _save_registry(reg)


def decided(job, fb, url):
    """這張卡該用哪一份平台履歷,由程式照這張卡現在挑的履歷、語言,和有沒有收下的客製履歷決定;不問 agent,也不看舊紀錄。
    {'platform', 'lang', 'variant', 'profile_kind', 'fixed_url'(固定版已登記才有), 'name'(平台上那份的名稱,記過才有)}"""
    import ship
    platform = profile_key(url)
    resume_id, lang = ship.resolve(job, fb)
    out = {'platform': platform, 'lang': lang, 'variant': resume_id,
           'profile_kind': 'custom' if has_custom_resume(job, fb) else 'fixed'}
    entry = where(platform, lang, resume_id) if platform and resume_id else None
    if entry and entry.get('read'):
        out['fixed_url'] = entry['read']
        if entry.get('name'):
            out['name'] = entry['name']
    return out


_LINK = re.compile(r'\[([^\]]*)\]\(([^)\s]+)\)')


KINDS = ('page', 'file')
LEGACY_FILE_EXT = ('.pdf', '.doc', '.docx')


@_registry_writer
def set_kind(platform, slot, kind):
    """使用者說登記的那一份是網頁(填表給拍照工具)還是檔案(不給)。"""
    if kind not in KINDS:
        raise ValueError(f'只能是 {"、".join(KINDS)}')
    reg = _load_registry()
    entry = (reg.get(platform) or {}).get(slot)
    if not isinstance(entry, dict):
        raise ValueError(f'{platform} 沒有登記 {slot}')
    entry['kind'] = kind
    _save_registry(reg)


def is_file_url(value):
    """登記的這一份是不是檔案:照使用者在登記表標的。程式自己登記的一律先當網頁;
    升級前登記的(沒有 kind)照舊版看副檔名,使用者不用補(AGENTS.md 規則表第 4 列)。"""
    value = str(value or '')
    entries = [entry for _platform, _lang, _variant, entry in slots()
               if value and value in (entry.get('read'), entry.get('edit'))]
    if any(entry.get('kind') == 'file' for entry in entries):
        return True
    path = value.split('#', 1)[0].split('?', 1)[0].lower()
    return any('kind' not in entry for entry in entries) and path.endswith(LEGACY_FILE_EXT)


def _safe_page_url(value):
    value = str(value or '').strip()
    if value.startswith(('http://localhost', 'http://127.0.0.1', 'http://[::1]')):
        return True
    return value.startswith('https://') and bool(_host(value))


@_registry_writer
def remember_application_history(platform, url):
    """保存 agent 回報的平台應徵紀錄頁;主機清單只用來挑材料。"""
    if not url or not _safe_page_url(url):
        return False
    reg = _load_registry()
    reg.setdefault('_application_records', {})[platform] = url
    _save_registry(reg)
    return True


def application_record_pages():
    """已知平台的應徵紀錄頁。"""
    records = registry().get('_application_records') or {}
    return [{'platform': str(platform), 'url': str(url)}
            for platform, url in records.items()
            if isinstance(url, str) and url and _safe_page_url(url)]


def _custom_documents(job, fb):
    import ship
    return [
        item for item in ship.documents(job, fb)
        if (item.get('entry') or {}).get('status') == 'accepted'
        or item.get('id') == 'resume:legacy'
    ]


def has_custom_resume(job, fb):
    return any(item.get('kind') == 'resume'
               for item in _custom_documents(job, fb))


def main():
    ap = argparse.ArgumentParser(description='查看每份平台履歷的同步狀態；執行同步請用 sync.py')
    ap.add_argument('--board')
    ap.add_argument('--hosts', nargs='+', metavar='平台 主機',
                    help='設定一個平台涵蓋哪些主機(子網域也算),例:--hosts 我的平台 jobs.example.com;只給平台就列出目前的')
    ap.add_argument('--kind', nargs=3, metavar=('平台', '語言/履歷', '|'.join(KINDS)),
                    help='登記的那一份是網頁還是檔案;檔案的話填表不給那一頁的拍照工具,例:--kind 我的平台 zh/general file')
    args = ap.parse_args()
    if args.kind:
        platform, slot, kind = args.kind
        set_kind(platform, slot, kind)
        print(f'{platform} {slot}: {kind}')
        return
    if args.hosts:
        platform, *hosts = args.hosts
        if hosts:
            set_hosts(platform, hosts)
        print(f"{platform}: {' '.join(platform_hosts(platform, _load_registry())) or '(沒有)'}")
        return
    for row in status(args.board):
        print(f"{row['platform']} {row['lang']}/{row['variant']}: {row['state']}")


if __name__ == '__main__':
    main()

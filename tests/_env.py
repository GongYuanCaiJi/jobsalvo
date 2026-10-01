"""測試一律跑在暫存資料夾:先設 JOBSALVO_HOME 再 import 任何 tools,正式看板和資料碰都碰不到。"""
import os, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, '..', 'tools'))
if 'JOBSALVO_TEST_HOME' not in os.environ:
    os.environ['JOBSALVO_TEST_HOME'] = tempfile.mkdtemp(prefix='jobsalvo-test-')
    home = os.environ['JOBSALVO_TEST_HOME']
    ex = os.path.join(HERE, '..', 'examples')
    for n in ('prefs.md', 'apply-rules.md', 'resume.md'):
        shutil.copyfile(os.path.join(ex, n), os.path.join(home, n))
    shutil.copyfile(os.path.join(HERE, 'fixtures', 'jobsalvo.json'), os.path.join(home, 'jobsalvo.json'))
os.environ['JOBSALVO_HOME'] = os.environ['JOBSALVO_TEST_HOME']
# Tests always create their own target. Do not let an inherited agent board redirect writes.
os.environ.pop('AGENT_BOARD', None)
os.environ.pop('JOBSALVO_BOARD_ID', None)
# 副本(測試的假看板)的證據夾放這次測試的暫存,不留在共用的 /tmp/jobsalvo(#315)
os.environ['EVIDENCE_TMP'] = os.path.join(os.environ['JOBSALVO_TEST_HOME'], 'tmp')
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
# agent 專用 Chrome 的連線設定預設在 ~/.cache/jobsalvo/agent-chrome.json(家目錄底下的絕對路徑,不跟著 JOBSALVO_HOME)。
# 不改掉的話,測試讀到的是真的那份,會真的去開 agent Chrome 的分頁。其他家目錄的東西(無頭 Chrome)照舊用真的。
import config as _cf  # noqa: E402
_cf.DEFAULTS['browser']['state'] = os.path.join(os.environ['JOBSALVO_TEST_HOME'], 'agent-chrome.json')
# agent 的 Chrome 資料夾也一樣:預設在 ~/Library/Application Support 底下,測試不准碰真的那一份。
_cf.DEFAULTS['browser']['data_dir'] = os.path.join(os.environ['JOBSALVO_TEST_HOME'], 'agent-chrome')
_cf.reload(os.environ['JOBSALVO_HOME'])
# Codex 自己的瀏覽器設定(允許哪些網站上傳、下載)在 ~/.codex 底下:環境檢查會讀它,測試不讀真的那一份
import agent_chrome as _ac  # noqa: E402
_ac.CODEX_BROWSER_CONFIG = os.path.join(os.environ['JOBSALVO_TEST_HOME'], 'codex-browser-config.toml')


def use_home(test, **overrides):
    """這個測試用一個假的資料夾當家目錄(test.tmp/home,裡面有 resume/):設定照預設值,
    overrides 逐段蓋過(例如 resume={'langs': ['zh']}),寫進 jobsalvo.json 並 reload。
    設好 test.tmp、test.home、test.settings;測試結束換回原本的家目錄、刪掉暫存。"""
    import copy, json
    from unittest import mock
    import config as cf
    test.tmp = test.enterContext(tempfile.TemporaryDirectory(prefix='jobsalvo-home-'))
    test.home = os.path.join(test.tmp, 'home')
    os.makedirs(os.path.join(test.home, 'resume'))
    test.settings = copy.deepcopy(cf.DEFAULTS)
    for key, value in overrides.items():
        test.settings[key].update(value)
    with open(os.path.join(test.home, cf.NAME), 'w', encoding='utf-8') as f:
        json.dump(test.settings, f, ensure_ascii=False)
    test.addCleanup(cf.reload, cf.HOME)
    test.enterContext(mock.patch.dict(os.environ, JOBSALVO_HOME=test.home))
    cf.reload(test.home)


def tiny_pdf(path, text='', pages=1):
    """寫一份合法的小 PDF(不靠套件):pages 頁,每頁一行 text(text 不同,內容就不同)。"""
    body = f'BT /F1 18 Tf 72 720 Td ({text}) Tj ET'.encode('ascii')
    kids = ' '.join(f'{4 + 2 * i} 0 R' for i in range(pages))
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', f'<< /Type /Pages /Kids [{kids}] /Count {pages} >>'.encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for i in range(pages):
        objects += [f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R '
                    '/Resources << /Font << /F1 3 0 R >> >> >>'.encode(),
                    b'<< /Length %d >>\nstream\n' % len(body) + body + b'\nendstream']
    out, offsets = bytearray(b'%PDF-1.4\n'), []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % i + obj + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    out += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'wb') as f:
        f.write(out)


def multipart(fields=None, files=()):
    """組 multipart/form-data:fields 是一般欄位,files 是 (欄位, 檔名, 內容) 的清單(都當 PDF)。回 (內容, Content-Type)。"""
    boundary = 'jobsalvo-test-boundary'
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}\r\n'.encode()
             for n, v in (fields or {}).items()]
    parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
              'Content-Type: application/pdf\r\n\r\n'.encode() + content + b'\r\n' for field, name, content in files]
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def make_board(path, fb=None, jobs=(), data=None, sty=':root{--a:1}', app='/*app v1*/'):
    """寫一份測試用的假看板:data 給了就整份用它,不然是 {'jobs': jobs};fb 是標記(dict)。回 path。"""
    import json, board_doc
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(board_doc.assemble(sty, '<b id="stat-first">0</b>', '', data or {'jobs': list(jobs)},
                                   json.dumps(fb or {}, ensure_ascii=False), app))
    return path


def read_board(path):
    """看板檔讀出來拆好(data、fb 都在裡面)。好幾個測試檔各寫過一份一模一樣的,收在這。"""
    import board_doc
    with open(path, encoding='utf-8') as f:
        return board_doc.parse(f.read())


def read_fb(path):
    """看板上的標記(fb)。"""
    import json
    return json.loads(read_board(path)['fb'])


def evidence_rounds(url, board=None):
    """那張卡的每一輪證據夾(資料夾路徑),舊的在前。"""
    import evidence
    home = evidence.card_dir(url, board)
    try:
        return [os.path.join(home, n) for n in sorted(os.listdir(home)) if evidence.ROUND.match(n)]
    except OSError:
        return []


def evidence_events(round_dir):
    """一輪證據的事件,照時間排;寫到一半的那一行(當掉)不算。"""
    import contextlib, json, evidence
    out = []
    try:
        with open(os.path.join(round_dir, evidence.EVENTS), encoding='utf-8') as f:
            for line in f:
                with contextlib.suppress(ValueError):
                    out.append(json.loads(line))
    except OSError:
        return []
    return sorted(out, key=lambda e: (str(e.get('at')), e.get('n') or 0))

# 測試絕不能真的開 agent 的 Chrome(#275:漏 mock 的測試開了兩個有視窗的 Chrome,留在他的 Dock 上)。
# 開法只有兩種(launch:open -n … --no-startup-window;show:Chrome --user-data-dir=… --new-window),碰到就當場失敗。
import subprocess as _sp  # noqa: E402


def _no_real_agent_chrome(real):
    def guard(args, *a, **k):
        argv = [str(x) for x in (args if isinstance(args, (list, tuple)) else [args])]
        if '--no-startup-window' in argv or ('--new-window' in argv and any(x.startswith('--user-data-dir=') for x in argv)):
            raise RuntimeError('測試不准真的開 agent 的 Chrome:把 agent_chrome.launch / show / wait_claude mock 掉')
        return real(args, *a, **k)
    return guard


_sp.run = _no_real_agent_chrome(_sp.run)
_sp.Popen = _no_real_agent_chrome(_sp.Popen)

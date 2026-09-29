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
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
# agent 專用 Chrome 的連線設定預設在 ~/.cache/jobsalvo/agent-chrome.json(家目錄底下的絕對路徑,不跟著 JOBSALVO_HOME)。
# 不改掉的話,測試讀到的是真的那份,會真的去開 agent Chrome 的分頁。其他家目錄的東西(無頭 Chrome)照舊用真的。
import config as _cf  # noqa: E402
_cf.DEFAULTS['browser']['state'] = os.path.join(os.environ['JOBSALVO_TEST_HOME'], 'agent-chrome.json')
# agent 的 Chrome 資料夾也一樣:預設在 ~/Library/Application Support 底下,測試不准碰真的那一份。
_cf.DEFAULTS['browser']['data_dir'] = os.path.join(os.environ['JOBSALVO_TEST_HOME'], 'agent-chrome')
_cf.reload(os.environ['JOBSALVO_HOME'])


def read_board(path):
    """看板檔讀出來拆好(data、fb 都在裡面)。好幾個測試檔各寫過一份一模一樣的,收在這。"""
    import board_doc
    with open(path, encoding='utf-8') as f:
        return board_doc.parse(f.read())


def read_fb(path):
    """看板上的標記(fb)。"""
    import json
    return json.loads(read_board(path)['fb'])

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

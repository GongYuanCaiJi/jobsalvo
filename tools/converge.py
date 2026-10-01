#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
converge —— 找新職缺一輪的入口。看板頁首「🔎 找新職缺」按哪一顆,就是哪個 --mode:
  deep  更深:使用者喜歡的公司現在的開缺 + 同型的缺在別家
  wide  更廣:板上還沒出現過、使用者可能喜歡的職能
  dir   指定方向:使用者打一句「往哪挖」(--direction,原話照抄)
  both  更深再更廣(不給 --mode 時)
實際的「找 → 程式清洗 → 判 → 進板」在 research.py;這支負責:
更新判準檔(feedback_dump)、回報進度(jobrun)、呼叫 research。
找缺時只把直連 HTTP 404/410 當成程式可判定的下架;其他由 agent 依擷取原文判斷。使用者要丟的卡用卡上的「🔧 有問題」「🗑 移除」。
進度寫在 converge_status.json,看板伺服器讀它回報。

硬排除(職稱一中就不送、不花 agent)與「要小心」的字(不丟,標出來交給判斷那一段)
在看板的「⚙ 設定」填(search.exclude_words / flag_words,一行一個字;進階可寫正規表示式 exclude_title / flag_title)。
那是使用者自己的政策:照他的原話寫,一處可稽核;寫之前拿他按過喜歡的缺掃一次,確定不會擋到喜歡的。

用法:
  python3 converge.py --mode dir --direction "遊戲反作弊"
  python3 converge.py --dry          # 只看這輪會從哪幾家公司列開缺,不派 agent
  python3 converge.py --seed-url <缺A> --why "他的原話"   # 找跟這幾張同型的(當成指定方向)
"""
import re,os,sys,subprocess,argparse,time
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import board_doc as bd
import jobrun
import config as cf
REPO=cf.HOME
TOOLS=cf.TOOLS
SP=cf.TMP
# 進度檔:main 和 _main 都要用(以前只在 main 裡定義,按停止收尾時 _main 用到就 NameError 當掉,「按了停止」標記清不掉)
STATUS=os.path.join(os.environ.get('CONVERGE_TMP') or SP,'converge_status.json')

_NEVER=re.compile(r'(?!x)x')      # 沒設定時:什麼都不中
def _re(words_key, regex_key):
    s=cf.C.get('search') or {}
    parts=[re.escape(w.strip()) for w in (s.get(words_key) or []) if str(w).strip()]
    flags = re.I
    if (s.get(regex_key) or '').strip():
        from settings_api import compile_match
        compiled = compile_match(s[regex_key].strip())
        parts.append(compiled.pattern)
        flags = compiled.flags
    return re.compile('|'.join(parts), flags) if parts else _NEVER
EXCLUDE_TITLE=_re('exclude_words','exclude_title')
FLAG_TITLE=_re('flag_words','flag_title')

MODE_NAME={'deep':'更深','wide':'更廣','dir':'指定方向'}

def main():
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--live',default=bd.LIVE)
    ap.add_argument('--dry',action='store_true')
    ap.add_argument('--mode',choices=['both','deep','wide','dir'],default='both')
    ap.add_argument('--direction',default='',help='--mode dir:他這輪要往哪挖(他的原話,原封不動交給 agent)')
    ap.add_argument('--seed-url',action='append',default=[],help='他指名「找類似這張的」:走更深,範圍是這幾張同型')
    ap.add_argument('--seed-co',action='append',default=[],help='他指名「找這家更多」:走更深,範圍縮到這幾家')
    ap.add_argument('--why',default='',help='配 --seed-url:他對這幾張的原話')
    ap.add_argument('--explore-only',action='store_true',help='等於 --mode wide(舊的寫法)')
    ap.add_argument('--limit',type=int,default=0,help='這一輪最多判幾張(看板上的「判幾張」)')
    ap.add_argument('--minutes',type=int,default=0,help='找的那一段最多跑幾分鐘(看板上的「__ 分鐘」);0 = 不限時')
    a=ap.parse_args()
    if a.explore_only: a.mode='wide'
    direction=re.sub(r'\s+',' ',a.direction).strip()[:300]
    T0=time.time()
    # graceful:看板按停止時先收工(只停 agent,這支把做完的收下),見 jobrun.control
    def st(phase,**kw):
        if not a.dry: jobrun.write(STATUS,dict(kw,phase=phase,pid=os.getpid(),mode=a.mode,direction=direction,t0=T0,minutes=a.minutes,graceful=True))
    jobrun.clear_finish(STATUS)
    jobrun.clear_paused(STATUS)
    # 時間只算「找」:從這輪開始算,暫停的時間扣掉;「更深＋更廣」兩段共用
    time_up=lambda: bool(a.minutes) and time.time()-T0-jobrun.paused_seconds(STATUS)>=a.minutes*60
    st('start')
    try:
        _main(a,direction,st,lambda:jobrun.finishing(STATUS),time_up)
    except SystemExit:
        raise
    except Exception as e:
        st('failed',msg=f'{type(e).__name__}: {e}',finished_at=time.time())
        try:   # 他不一定開著 🔎 面板;沒跑完要寫進每一頁最上面的「📣 agent 回報」
            import agent_report
            agent_report.report('找新職缺',f'這輪沒跑完:{type(e).__name__}: {e}',
                                need='可以再按一次;一直失敗就在「🔎 找新職缺」按「看紀錄」',live=a.live)
        except Exception: pass  # noqa: BLE001, S110 — 原因上面已經寫進進度,下一行照樣丟出去;看板回報只是再提醒一次
        raise

def _main(a,direction,st,finishing=lambda:False,time_up=lambda:False):
    import prefs, research
    fb,jobs=prefs.load(a.live)
    # 他在看板上指名的種子(某張缺、某家公司)。這不是第四種找法:就是「更深」,範圍由他指定。
    # 以前 --seed-url 會把整輪轉成 dir(指定方向),等於同一件事有兩套邏輯;現在統一走 deep。
    seeds={'co':list(a.seed_co),'job':list(a.seed_url)}
    if a.seed_url or a.seed_co:
        a.mode='deep'
    if a.dry:
        cs=prefs.cards(fb,jobs)
        print(f'他表過態的卡 {len(cs)} 張(喜歡類 {sum(c["pos"] for c in cs)})。更深會叫 agent 去看這幾家現在開什麼:')
        for r in research.liked_company_list(cs): print(f"  {r['company']}")
        print('--dry:只算不派 agent'); return
    if a.mode=='dir' and not direction:
        st('nothing',msg='沒有寫要往哪個方向挖。'); print('沒有寫要往哪個方向挖。'); sys.exit(0)
    # 判準檔跟著板上標記更新(給人看、也給想多了解他口味的 agent 讀;判斷那一段不整包讀它)
    subprocess.run([sys.executable,os.path.join(TOOLS,'feedback_dump.py'),'--live',a.live],cwd=REPO)
    added=0
    tally={'found':0,'dropped':0}
    for m in (['deep','wide'] if a.mode=='both' else [a.mode]):
        if finishing() or time_up(): break        # 他按了停止、或時間用完了:不再開下一種找法
        added+=research.run(m,direction if m=='dir' else '',live=a.live,browser_required=True,st=st,seeds=seeds,
                            limit=a.limit,finishing=finishing,minutes=a.minutes,time_up=time_up,tally=tally)
    # 新缺補上架日:ATS 有日期就直接用;其餘 agent 只讀程式擷取文字,已處理網址不重抓。
    subprocess.run([sys.executable,os.path.join(TOOLS,'posted_age.py'),
                    '--board',a.live],cwd=REPO,check=False)
    print(f'這輪新增 {added} 筆職缺(在看板「🆕 待評估」)')
    if finishing():
        st('stopped',added=added,pending=research.pending_count(),finished_at=time.time(),msg='你按了停止,做完的收下了')
        jobrun.clear_finish(STATUS)
        return
    # 照實講這輪:找到幾張、清洗掉幾張、進看板幾張;時間到停的也講
    msg=(('時間到,這輪沒找到' if not tally['found'] else '時間到,停止找新的,找到的都判完了') if time_up() else '')
    st('done',added=added,found=tally['found'],dropped=tally['dropped'],timeup=time_up(),msg=msg,finished_at=time.time())

if __name__=='__main__': main()

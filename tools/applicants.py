#!/usr/bin/env python3
"""更新板上卡片的應徵人數:每輪找缺結束時 converge 自動跑,不用手動。

抓得到的:104「兩週內應徵人數」、LinkedIn 公開頁的應徵者人數、頁內有 applicant_count 的站(remote3 等)。
  有人數的卡   → 重抓;數字變了記進 applicants_log(看得出是剛上架還是冷門)
  還沒查過的卡 → 查一次;頁面沒寫人數的記下查過的日期(applicants_at),之後不再重查
只走官方資料或直連一次,不開瀏覽器。
"""
import os, sys, time, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_doc as bd
import page_fetch


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--board', default=bd.LIVE)
    a = ap.parse_args(argv)
    with open(a.board, encoding='utf-8') as f:
        jobs = bd.parse(f.read())['data']['jobs']
    todo = [j['id'] for j in jobs if str(j.get('id', '')).startswith('http') and not j.get('dead')
            and (j.get('applicants') or not j.get('applicants_at'))]
    pages = dict(zip(todo, page_fetch.fetch_many(todo, page_fetch.fetch_light)))
    today = time.strftime('%Y-%m-%d')
    changed = []

    def update(data, _fb):
        for job in data['jobs']:
            page = pages.get(job.get('id'))
            if not page or page.status == 'unknown':   # 抓不到(被擋、逾時)不算查過,下一輪再試
                continue
            now = page.applicants   # 下架的頁(closed)沒有人數:跟頁面沒寫一樣記下查過
            if not now:
                if not job.get('applicants'):
                    job['applicants_at'] = today
                    changed.append(job['id'])
                continue
            log = job.get('applicants_log') or (
                [[job.get('applicants_at') or '', job['applicants']]] if job.get('applicants') else [])
            if not log or log[-1][1] != now:   # 數字變了才多記一筆
                log.append([today, now])
            job['applicants'], job['applicants_at'], job['applicants_log'] = now, today, log
            changed.append(job['id'])
    if any(p.status != 'unknown' for p in pages.values()):
        bd.set_data(update, live=a.board)
    print(f'應徵人數:查了 {len(todo)} 張,更新 {len(changed)} 張')
    return len(changed)


if __name__ == '__main__':
    main()

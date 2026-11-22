"""一場面試要帶去的四份,合成一個 PDF,順序固定:自己的履歷、平台履歷、附件、面試備忘。

- 自己的履歷、附件:這張卡寄出(或會寄出)的那幾份(ship.documents),跟投遞用同一套規則。
- 面試備忘:iv_sheet 排的那份。
- 平台履歷(104 等):還不會自動取得(issue #736)。放法:把 PDF 放在 <資料夾>/平台履歷/<卡片編號>.pdf
  (編號 = card.card_id_from_url);放的人用 `python3 tools/iv_pack.py <職缺網址> <PDF>`。沒放就印一頁「平台履歷:尚未放入」,說清楚放哪裡,不靜默少一份。
"""
import os, shutil, tempfile

import card
import config as cf
import iv_sheet
import markdown_pdf
import pdf_tools
import ship

SLOT_DIR = '平台履歷'


def slot_path(job_id, home=None):
    """這場的平台履歷 PDF 該放在哪(不管有沒有放)。"""
    return os.path.join(home or cf.HOME, SLOT_DIR, card.card_id_from_url(job_id) + '.pdf')


def _empty_page(title, body, work, name):
    md = os.path.join(work, name + '.md')
    with open(md, 'w', encoding='utf-8') as fh:
        fh.write(f'# {title}\n\n{body}\n')
    pdf = os.path.join(work, name + '.pdf')
    markdown_pdf.render(md, pdf, iv_sheet.STYLE, 'zh')
    return pdf


def _doc_pdf(doc, work):
    """一份文件的 PDF:排好的在就用,沒有就現排(Markdown 原稿)。"""
    path = doc.get('effective_path') or ''
    if path and os.path.isfile(path):
        return path
    src = doc.get('custom_path') if doc.get('custom') else doc.get('source')
    if not src or not os.path.isfile(src):
        raise markdown_pdf.RenderError(f"找不到「{doc.get('name')}」的檔案")
    if not markdown_pdf.is_markdown(src):
        return src
    out = os.path.join(work, f"{doc['id'].replace('/', '_')}.pdf")
    markdown_pdf.render(src, out, doc.get('style_path') or None, 'zh')
    return out


def pack_bytes(data, fb, job_id, home=None):
    """合成的 PDF 位元組。哪一格空著,就在那個位置印一頁說明,不跳過。"""
    home = home or cf.HOME
    job = iv_sheet._job(data, job_id)
    docs = ship.documents(job, fb)
    own = [d for d in docs if d['kind'] == 'resume']
    atts = [d for d in docs if d['kind'] == 'attachment']
    slot = slot_path(job_id, home)
    with tempfile.TemporaryDirectory(prefix='.iv-pack-') as work:
        files = [_doc_pdf(d, work) for d in own] or [_empty_page(
            '自己的履歷:尚未放入', '這張卡還沒挑好履歷,所以這一格是空的。先在卡片上挑一份履歷。', work, 'own')]
        files.append(slot if os.path.isfile(slot) else _empty_page(
            '平台履歷:尚未放入',
            f'平台履歷目前不會自動取得。要補的話,執行 `python3 tools/iv_pack.py <職缺網址> <平台履歷 PDF>`,\n'
            f'檔案會放到 `{slot}`,再按一次「印出四份」就會合進來。', work, 'platform'))
        files += [_doc_pdf(d, work) for d in atts]
        memo = os.path.join(work, 'memo.pdf')
        with open(memo, 'wb') as fh:
            fh.write(iv_sheet.pdf_bytes(data, job_id, home))
        files.append(memo)
        out = os.path.join(work, 'pack.pdf')
        pdf_tools.merge(files, out)
        with open(out, 'rb') as fh:
            return fh.read()


def put(job_id, pdf_path, home=None):
    """把平台履歷 PDF 放進這場的空格(人或 agent 都走這裡)。回放到哪。"""
    with open(pdf_path, 'rb') as fh:
        if fh.read(5) != b'%PDF-':
            raise ValueError('這不是 PDF')
    dest = slot_path(job_id, home)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(pdf_path, dest)
    return dest


def main():
    import argparse
    ap = argparse.ArgumentParser(description='把平台履歷 PDF 放進一場面試的「印出四份」')
    ap.add_argument('job', help='職缺網址(看板上那張卡的網址)')
    ap.add_argument('pdf', help='平台履歷的 PDF 檔')
    a = ap.parse_args()
    print(put(a.job, a.pdf))


if __name__ == '__main__':
    main()

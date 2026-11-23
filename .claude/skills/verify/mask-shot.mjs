export const MASK = [
  '.fitline', '.srcwhy', '.vd', '.sr.fitrow', '.vdmini', '.vdmini-why', '.vdsum', '.vd-agent', '.vd-ship',
  'details.cmore .sum', 'textarea', '.rzm-box .cust-list', '#rzmodal label', '.rd-body',
  '.iv-ans', '.iv-script', '.iv-qf', '.iv-focus', '.iv-ask', '.iv-say', '.iv-round-w', '.iv-cite',
  '.fm-line.rp-auto .q', '.cfg-file', '.ib-msg', '.ib-do', '.ib-full', '.ib-said', '.ansq-why', '.ansq-en', '.wt-l',
];

export async function shot(page, path, { extra = [], text = null } = {}) {
  await page.evaluate(src => {
    const re = src && new RegExp(src, 'i'); // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp  驗收 agent 自己寫的私密詞,只跑在本機驗收頁
    document.querySelectorAll('#app *').forEach(e => {
      const own = re && [...e.childNodes].some(n => n.nodeType === 3 && re.test(n.nodeValue));   // 有子元素的列，私密字在它自己的文字裡
      if (re && (own || (!e.children.length && re.test(e.textContent)) || (e.tagName === 'INPUT' && re.test(e.value)))) e.classList.add('vm-x');
    });
    document.querySelectorAll('.sr').forEach(r => { if (/彈藥|為什麼適合/.test(r.querySelector('.srk')?.textContent || '')) r.classList.add('vm-x'); });
  }, text);
  await page.evaluate(sels => {
    document.querySelectorAll(sels.concat('.vm-x').join(',')).forEach(e => {
      if (e.id === 'cfg-st') return;
      e.dataset.vmask = 1;
      e.style.background = '#6b7280';
      e.style.color = 'transparent';
      e.querySelectorAll('*').forEach(c => { c.style.visibility = 'hidden'; });
    });
  }, MASK.concat(extra));
  try {
    return await page.screenshot({ path });
  } finally {
    await page.evaluate(() => {
      document.querySelectorAll('[data-vmask]').forEach(e => {
        e.style.background = '';
        e.style.color = '';
        e.querySelectorAll('*').forEach(c => { c.style.visibility = ''; });
        delete e.dataset.vmask;
      });
      document.querySelectorAll('.vm-x').forEach(e => e.classList.remove('vm-x'));
    });
  }
}

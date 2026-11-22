import sys; sys.path.insert(0,'tests')
import test_no_page_judgment as t
CASES = {
 'same_host': "from urllib.parse import urlparse\ndef f(a,b):\n    return urlparse(a).hostname == urlparse(b).hostname\n",
 'netloc_eq': "from urllib.parse import urlsplit\ndef f(a,b):\n    return urlsplit(a).netloc == urlsplit(b).netloc\n",
 'host_key':  "def host_key(u):\n    from urllib.parse import urlsplit\n    return urlsplit(u).hostname\ndef f(a,b):\n    return host_key(a) != host_key(b)\n",
 'zh_in_text':"def f(text):\n    return '已下架' in text\n",
 'agent_claim_and_page_status': (
     "def f(said, page_status):\n"
     "    if said.startswith('抓不到 JD') and page_status == 'ok':\n"
     "        return False\n"
 ),
 'normalized_url_keys': (
     "from urllib.parse import urlsplit\n"
     "def f(a, b):\n"
     "    a_key = urlsplit(a).path.rstrip('/')\n"
     "    b_key = urlsplit(b).path.rstrip('/')\n"
     "    return a_key == b_key\n"
 ),
 'second_page_parts_read': (
     "def _cited_text(cited):\n"
     "    text = '\\n'.join(part.text for part in cited.parts)\n"
     "    return cited.parts[0].text\n"
 ),
}
if __name__ == '__main__':
    for k,src in CASES.items():
        print(k, sorted(r[2] for r in t._r1_r2_r5('tools/x.py', src)))

"""dev_arch —— 量「改一處要動多處」:git 歷史和依賴圖,開發用,使用者的功能不會叫它。"""
import argparse, ast, collections, itertools, json, os, re, subprocess, sys, warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')
BOARD_JS = 'board.js'
KINDS = ('top', 'lazy', 'type', 'subprocess', 'http')
# code-maat 的預設門檻與算法(github.com/adamtornhill/code-maat,src/code_maat/analysis/logical_coupling.clj)
MIN_REVS, MIN_SHARED, MIN_COUPLING, MAX_CHANGESET = 5, 5, 0.30, 30
CODE = re.compile(r'^(tools/.+\.py|board/[^/]+\.(js|css|html))$')
BUGFIX = re.compile(r'^(fix\b|修)', re.I)
DATAFILE = re.compile(r"""['"]([\w.-]+\.(?:json|jsonl|html|tsv|csv|md))['"]""")
SCRIPT = re.compile(r'(?:^|/)([a-z_]+)\.py$')


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def modules(root=TOOLS):
    out = {}
    for d, _, files in os.walk(root):
        rel = os.path.relpath(d, root)
        if rel != '.' and (rel.startswith('.') or '__pycache__' in rel):
            continue
        pkg = [] if rel == '.' else rel.split(os.sep)
        for f in files:
            if f.endswith('.py'):
                stem = f[:-3]
                out['.'.join(pkg if stem == '__init__' else pkg + [stem])] = os.path.join(d, f)
    return out


def _targets(node, me, names):
    if isinstance(node, ast.Import):
        cands = [a.name for a in node.names]
    else:
        base = node.module or ''
        if node.level:
            pkg = me.split('.') if names[me].endswith('__init__.py') else me.split('.')[:-1]
            pkg = pkg[:len(pkg) - (node.level - 1)]
            base = '.'.join([*pkg, base] if base else pkg)
        cands = [full if full in names else base for full in (f'{base}.{a.name}' if base else a.name for a in node.names)]
    out = set()
    for c in cands:
        while c and c not in names:
            c = c.rpartition('.')[0]
        if c and c != me:
            out.add(c)
    return out


def _parse(text, path):
    with warnings.catch_warnings():  # 字串裡的 JS 正規式跳脫字元會讓 parse 喊 SyntaxWarning,跟依賴無關
        warnings.simplefilter('ignore', SyntaxWarning)
        return ast.parse(text, path)


def _scripts(node):
    """寫在 f-string 裡的不算:那是給 agent 照著跑的指示文字,不是這個程式自己開的。"""
    inside = {id(c) for f in ast.walk(node) if isinstance(f, ast.JoinedStr) for c in ast.walk(f)}
    return {m.group(1) for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in inside for m in [SCRIPT.search(n.value)] if m}


def _launches(tree):
    """開子程序跑的 'x.py':流進 subprocess / Popen / os.exec 呼叫的參數才算。參數是這個檔裡的變數、
    這個檔裡函式的回傳值,就照指派或回傳的內容追;把參數交給這類呼叫的本檔函式也算開子程序。"""
    def launcher(call):
        func = ast.unparse(call.func)
        return func.startswith(('subprocess.', 'os.exec')) or func.endswith('Popen') or func in wraps

    def reach(node):
        out = set(_scripts(node))
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                out |= held.get(n.id, set())
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                out |= returns.get(n.func.id, set())
        return out
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    held, returns, wraps = collections.defaultdict(set), collections.defaultdict(set), set()
    for _ in range(3):  # 變數接變數、函式回傳變數:追三層就收斂
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                held[n.targets[0].id] |= reach(n.value)
        for f in funcs:
            returns[f.name] |= set().union(*(reach(r.value) for r in ast.walk(f) if isinstance(r, ast.Return) and r.value))
            params = {a.arg for a in f.args.args + f.args.kwonlyargs}
            if any(isinstance(c, ast.Call) and launcher(c) and params & {x.id for x in ast.walk(c) if isinstance(x, ast.Name)}
                   for c in ast.walk(f)):
                wraps.add(f.name)
    return set().union(*(reach(ast.Tuple([*c.args, *(k.value for k in c.keywords)])) for c in ast.walk(tree)
                         if isinstance(c, ast.Call) and launcher(c)))


def _imports(tree, me, names):
    out = set()

    def walk(node, kind):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.Import, ast.ImportFrom)):
                out.update((t, kind) for t in _targets(ch, me, names))
            if isinstance(ch, ast.If) and 'TYPE_CHECKING' in ast.unparse(ch.test):
                for b in ch.body:
                    walk(ast.Module([b], []), 'type') if not isinstance(b, (ast.Import, ast.ImportFrom)) else \
                        out.update((t, 'type') for t in _targets(b, me, names))
                for b in ch.orelse:
                    walk(ast.Module([b], []), kind)
                continue
            walk(ch, 'lazy' if kind == 'lazy' or isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) else kind)
    walk(tree, 'top')
    return out


def graph(root=ROOT):
    names = modules(os.path.join(root, 'tools'))
    names.pop('dev_arch', None)  # 量的工具自己不算
    edges, files_of = set(), collections.defaultdict(set)
    loc = {}
    for me, path in names.items():
        text = _read(path)
        loc[os.path.relpath(path, root)] = text.count('\n')
        tree = _parse(text, path)
        best = {}
        for t, k in _imports(tree, me, names):
            best[t] = min(k, best.get(t, k), key=('top', 'lazy', 'type').index)
        edges.update((me, t, k) for t, k in best.items())
        edges.update((me, s, 'subprocess') for s in _launches(tree) if s in names and s != me)
        if me != 'board_server' and '/api/' in text and re.search(r'urlopen|http\.client|HTTPConnection', text):
            edges.add((me, 'board_server', 'http'))
        for f in DATAFILE.findall(text):
            files_of[f].add(me)
    board = os.path.join(root, 'board')
    for f in sorted(os.listdir(board)) if os.path.isdir(board) else []:
        if CODE.match('board/' + f):
            loc['board/' + f] = _read(os.path.join(board, f)).count('\n')
    js = os.path.join(board, BOARD_JS)
    if os.path.exists(js) and re.search(r'fetch\([^)]*/api/', _read(js)):
        edges.add((BOARD_JS, 'board_server', 'http'))
    nodes = sorted(set(names) | ({BOARD_JS} if os.path.exists(js) else set()))
    shared = {f: sorted(ms) for f, ms in sorted(files_of.items()) if len(ms) > 1}
    return {'edges': edges, 'shared': shared, 'nodes': nodes, 'loc': loc}


def adjacency(edges, kinds=KINDS):
    adj = collections.defaultdict(set)
    for a, b, k in edges:
        if k in kinds:
            adj[a].add(b)
    return adj


def sccs(nodes, adj):
    index, low, on, stack, out, n = {}, {}, set(), [], [], 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(adj[root])))]
        index[root] = low[root] = n; n += 1; stack.append(root); on.add(root)
        while work:
            v, it = work[-1]
            w = next(it, None)
            if w is not None:
                if w not in index:
                    index[w] = low[w] = n; n += 1; stack.append(w); on.add(w)
                    work.append((w, iter(sorted(adj[w]))))
                elif w in on:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop(); on.discard(w); comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))
    return sorted(out, key=lambda c: (-len(c), c))


class _OutOfBudget(Exception):
    pass


def _min_hitting(cycles, known, budget):
    """最少幾條邊能碰到每一個環(分支定界);known 是已知碰得到全部的一組,當起點上限。"""
    best, seen = set(known), [0]

    def go(chosen):
        seen[0] += 1
        if seen[0] > budget:
            raise _OutOfBudget
        unhit = [c for c in cycles if not c & chosen]
        if not unhit:
            best.clear(); best.update(chosen)
            return
        lower, used = 0, set()
        for c in sorted(unhit, key=len):
            if not c & used:
                lower += 1; used |= c
        if len(chosen) + lower >= len(best):
            return
        for e in sorted(min(unhit, key=len)):
            go(chosen | {e})
    go(frozenset())
    return best


def feedback_edges(nodes, adj, budget=200_000):
    """讓依賴變單向要切的邊:(下限, 切哪些)。每個區塊各自求最少(只對目前找到的環求最少,切完還有環就把它加進來重求);
    算不完就退回貪婪排序切的那組和不相交環數的下限。"""
    alive = set(nodes)
    out_ = {v: set(adj[v]) & alive for v in nodes}
    in_ = {v: set() for v in nodes}
    for v in nodes:
        for w in out_[v]:
            in_[w].add(v)
    head, tail = [], []
    while alive:
        moved = True
        while moved:
            moved = False
            for v in sorted(alive):
                if not out_[v] & alive:
                    tail.append(v); alive.discard(v); moved = True
                elif not in_[v] & alive:
                    head.append(v); alive.discard(v); moved = True
        if alive:
            v = max(sorted(alive), key=lambda x: len(out_[x] & alive) - len(in_[x] & alive))
            head.append(v); alive.discard(v)
    order = head + tail[::-1]
    pos = {v: i for i, v in enumerate(order)}
    cut = sorted((a, b) for a in nodes for b in adj[a] if b in pos and pos[b] < pos[a])
    try:
        best = set()
        for comp in sccs(nodes, adj):
            inside = set(comp)
            known = {e for e in cut if e[0] in inside and e[1] in inside}
            chosen, cycles = set(), []
            while True:
                cyc = _shortest_cycle(comp, {v: {w for w in adj[v] & inside if (v, w) not in chosen} for v in comp})
                if not cyc:
                    break
                cycles.append(frozenset(zip(cyc, cyc[1:] + cyc[:1])))
                chosen = _min_hitting(cycles, known, budget)
            best |= chosen
        return len(best), sorted(best)
    except _OutOfBudget:
        pass
    left = {v: set(adj[v]) for v in nodes}
    lower = 0
    while True:
        cyc = _shortest_cycle(nodes, left)
        if not cyc:
            break
        lower += 1
        for a, b in zip(cyc, cyc[1:] + cyc[:1]):
            left[a].discard(b)
    return lower, cut


def _shortest_cycle(nodes, adj):
    best = None
    for s in nodes:
        prev, q = {s: None}, collections.deque([s])
        while q:
            v = q.popleft()
            for w in sorted(adj.get(v, ())):
                if w == s:
                    path = [v]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    path = path[::-1]
                    if best is None or len(path) < len(best):
                        best = path
                    q.clear(); break
                if w not in prev:
                    prev[w] = v; q.append(w)
    return best


def propagation_cost(nodes, adj):
    total = 0
    for s in nodes:
        seen, q = {s}, [s]
        while q:
            for w in adj.get(q.pop(), ()):
                if w not in seen:
                    seen.add(w); q.append(w)
        total += len(seen)
    return total / (len(nodes) ** 2) if nodes else 0.0


def history(root=ROOT):
    raw = subprocess.run(['git', '-C', root, 'log', '--no-merges', '-M', '--name-status', '--format=%x00%s'],
                         capture_output=True, text=True, check=True).stdout
    alias, sets = {}, []
    for block in raw.split('\0')[1:]:
        subject, _, body = block.partition('\n')
        files = set()
        for line in body.splitlines():
            parts = line.split('\t')
            if len(parts) == 3 and parts[0].startswith('R'):
                old, new = parts[1], parts[2]
                alias[old] = alias.get(new, new)
                files.add(alias[old])
            elif len(parts) == 2:
                files.add(alias.get(parts[1], parts[1]))
        sets.append((subject, files))
    changes, bugfix = collections.Counter(), collections.Counter()
    for subject, files in sets:
        changes.update(files)
        if BUGFIX.match(subject):
            bugfix.update(files)
    return {'commits': len(sets), 'changes': changes, 'bugfix': bugfix, 'sets': sets}


def co_change(sets, current):
    revs, shared = collections.Counter(), collections.Counter()
    for _, files in sets:
        code = sorted(f for f in files if f in current)
        if not code or len(files) > MAX_CHANGESET:
            continue
        revs.update(code)
        shared.update(itertools.combinations(code, 2))
    out = []
    for (a, b), n in shared.items():
        average = (revs[a] + revs[b]) / 2
        if n < MIN_SHARED or average < MIN_REVS:
            continue
        degree = n / average
        if degree >= MIN_COUPLING:
            out.append((a, b, n, round(degree, 2)))
    return sorted(out, key=lambda r: (-r[3], -r[2], r[0], r[1]))


def group_commits(sets, files, recent=400):
    """同時改到 files 裡兩個以上的 commit 數:(最近 recent 個裡, 全部);一次改超過 MAX_CHANGESET 個檔的不算。"""
    hits = [len(set(files) & changed) >= 2 and len(changed) <= MAX_CHANGESET for _, changed in sets]
    return sum(hits[:recent]), sum(hits)


def file_of(node):
    return 'board/' + node if node == BOARD_JS else 'tools/' + node.replace('.', '/') + (
        '/__init__.py' if node == 'web' else '.py')


def measure(root=ROOT):
    g = graph(root)
    nodes, current = g['nodes'], set(g['loc'])
    h = history(root)
    views = {'開頭 import': ('top',), '加函式內 import': ('top', 'lazy'), '全部(再加只給型別用的、子程序、HTTP)': KINDS}
    deps = {}
    for label, kinds in views.items():
        adj = adjacency(g['edges'], kinds)
        lower, cut = feedback_edges(nodes, adj)
        deps[label] = {'edges': sum(len(v) for v in adj.values()), 'sccs': sccs(nodes, adj),
                       'cut_lower': lower, 'cut': cut, 'propagation_cost': round(propagation_cost(nodes, adj), 3)}
    linked = {frozenset((file_of(a), file_of(b))) for a, b, _ in g['edges']}
    for ms in g['shared'].values():
        linked.update(frozenset((file_of(a), file_of(b))) for a, b in itertools.combinations(ms, 2))
    pairs = co_change(h['sets'], current)
    hot = sorted(((h['changes'][f] * n, f, h['changes'][f], n, h['bugfix'][f]) for f, n in g['loc'].items()), reverse=True)
    kinds = collections.Counter(k for _, _, k in g['edges'])
    return {
        'nodes': len(nodes), 'edge_kinds': dict(sorted(kinds.items())), 'edges': sorted(g['edges']), 'deps': deps,
        'shared_files': g['shared'], 'commits': h['commits'],
        'hotspots': [{'file': f, 'changes': c, 'loc': n, 'score': s, 'bugfix': b} for s, f, c, n, b in hot],
        'co_change': [{'a': a, 'b': b, 'shared': n, 'degree': d, 'linked': frozenset((a, b)) in linked}
                      for a, b, n, d in pairs],
    }


def cut_size(d):
    lower, upper = d['cut_lower'], len(d['cut'])
    return str(upper) if lower == upper else f'{lower}–{upper}(沒算完,下限–上限)'


def report(m, top=15):
    cmd = 'uv run python tools/dev_arch.py'
    L = [f'# 架構基線\n\n每個數字都由 `{cmd}` 產生(`--json` 是同一份數字)。'
         f'依賴圖有 {m["nodes"]} 個點,邊:' + '、'.join(f'{k} {v}' for k, v in m['edge_kinds'].items()) + '。\n',
         '## 依賴圖\n', '| 算哪些邊 | 邊數 | 強連通區塊(大小) | 要切的最少邊 | propagation cost |', '|---|---|---|---|---|']
    for label, d in m['deps'].items():
        sizes = '、'.join(str(len(c)) for c in d['sccs']) or '沒有'
        L.append(f'| {label} | {d["edges"]} | {sizes} | {cut_size(d)} | {d["propagation_cost"]} |')
    for label, d in m['deps'].items():
        L.append(f'\n### {label}\n')
        if not d['sccs']:
            L.append('- 沒有環')
        for c in d['sccs']:
            L.append(f'- 區塊({len(c)}):' + '、'.join(f'`{x}`' for x in c))
        if d['cut']:
            L.append(('- 要切的邊(最少的其中一組):' if d['cut_lower'] == len(d['cut']) else '- 要切的邊(貪婪排序切的那組,不一定最少):') + '、'.join(f'`{a}`→`{b}`' for a, b in d['cut']))
    L += [f'\n## 熱點(全部 {m["commits"]} 個非 merge commit 的改動次數 × 現在行數)\n',
          '| 檔 | 改動次數 | 行數 | 分數 | 修 bug 的 commit(標題開頭 fix/修,下限) |', '|---|---|---|---|---|']
    L += [f'| `{r["file"]}` | {r["changes"]} | {r["loc"]} | {r["score"]} | {r["bugfix"]} |' for r in m['hotspots'][:top]]
    loose = [r for r in m['co_change'] if not r['linked']]
    L += [f'\n## 一起改的檔對(code-maat 門檻,共 {len(m["co_change"])} 對;其中 {len(loose)} 對彼此沒有依賴也不共用檔)\n',
          '| 檔 A | 檔 B | 共同次數 | 耦合度 | 有依賴或共用檔 |', '|---|---|---|---|---|']
    L += [f'| `{r["a"]}` | `{r["b"]}` | {r["shared"]} | {r["degree"]} | {"是" if r["linked"] else "**否**"} |'
          for r in m['co_change'][:top * 2]]
    L += [f'\n## 共用的資料檔({len(m["shared_files"])} 個檔名出現在兩個以上的模組)\n']
    L += [f'- `{f}`:' + '、'.join(f'`{x}`' for x in ms) for f, ms in m['shared_files'].items()]
    return '\n'.join(L) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--json', action='store_true', help='印 JSON(前後對照用)')
    ap.add_argument('--top', type=int, default=15, help='熱點列幾個(一起改的列兩倍)')
    ap.add_argument('--root', default=ROOT, help='量哪一份 checkout(前後對照時指向改之前那一份)')
    ap.add_argument('--group', nargs='+', metavar='FILE', help='只印:同時改到這幾個檔裡兩個以上的 commit 數')
    ap.add_argument('--recent', type=int, default=400, help='--group 的「最近」算幾個 commit')
    a = ap.parse_args(argv)
    if a.group:
        recent, total = group_commits(history(a.root)['sets'], a.group, a.recent)
        print(f'最近 {a.recent} 個 commit:{recent};全部:{total}')
        return
    m = measure(a.root)
    sys.stdout.write(json.dumps(m, ensure_ascii=False, indent=1, default=sorted) + '\n' if a.json else report(m, a.top))


if __name__ == '__main__':
    main()

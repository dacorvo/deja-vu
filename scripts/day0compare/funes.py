# Score Hugging Face funes over the day0bench corpus. funes indexes only
# .funes.jsonl turns files, which each agent's separate integration converts
# from that agent's sessions. `funes add claude` would also install the
# integration's hooks into Claude Code, and its converter needs jq, so the
# build writes those turns files itself from the Claude layout (the same
# sessions sit under both roots) and indexes them. Then every question goes
# to the recall tool of one `funes mcp` server, and the answer session's id is
# looked for in the hits, in rank order. Usage:
#   FUNES=/path/to/funes python3 funes.py <keep-dir>
# The header line of a hit shortens the id to eight characters; the full one
# is on the line under it:
#   → get answer_355c48bb --from 0 --to 3
import glob, json, os, re, shutil, subprocess, sys, time
S = sys.argv[1]
FUNES = os.environ.get('FUNES', 'funes')
home = os.path.join(S, 'home')
env = dict(os.environ, HOME=home, FUNES_HOME=os.path.join(home, '.funes'))
corpus = os.path.join(home, '.claude', 'projects')
turns = os.path.join(S, 'funes-turns')
memory = os.path.join(home, '.funes', 'memory')
t0 = time.time()
if not os.path.exists(memory):
    srcs = glob.glob(os.path.join(corpus, '*', '*.jsonl'))
    with open(srcs[0]) as f:
        if 'uuid' not in json.loads(f.readline()):
            sys.exit('the corpus has no message uuids: lay it down again with the current day0bench')
    os.makedirs(turns, exist_ok=True)
    for src in srcs:
        sid = os.path.basename(src)[:-len('.jsonl')]
        out = os.path.join(turns, sid + '.funes.jsonl')
        seq = 0
        with open(src) as f, open(out, 'w') as o:
            for r in map(json.loads, f):
                c = r['message']['content']
                texts = [c] if isinstance(c, str) else [b['text'] for b in c if b['type'] == 'text']
                blocks = [{'block_type': 'text', 'text': t} for t in texts if t.strip()]
                if blocks:
                    o.write(json.dumps({'format': 1, 'session_id': sid, 'turn_uuid': r['uuid'], 'seq': seq,
                                        'ts': r['timestamp'], 'role': r['message']['role'],
                                        'blocks': blocks, 'harness': 'claude'}) + '\n')
                    seq += 1
        # Keep the transcript's mtime: funes indexes a directory newest file first.
        shutil.copystat(src, out)
    print('converted', len(srcs), f'transcripts in {time.time() - t0:.0f}s')
    r = subprocess.run([FUNES, 'index', turns, '--yes', '--no-thinking'],
                       capture_output=True, text=True, env=env)
    print('index rc', r.returncode, (r.stdout + r.stderr)[-400:])
    # A rejected turns file fails the run but leaves the rest indexed: stop
    # rather than score a partial index.
    if r.returncode:
        sys.exit(1)
build = time.time() - t0
qs = json.load(open(os.path.join(S, 'questions.json')))
# A search is timed from a warm process, as the page says: the server loads
# the models on the first question and keeps them.
mcp = subprocess.Popen([FUNES, 'mcp'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, text=True, env=env)
def send(msg):
    mcp.stdin.write(json.dumps(dict(msg, jsonrpc='2.0')) + '\n')
    mcp.stdin.flush()
def call(n, method, params):
    send({'id': n, 'method': method, 'params': params})
    for line in mcp.stdout:
        m = json.loads(line)
        if m.get('id') == n:
            if 'error' in m:
                sys.exit(f'funes mcp {method}: {m["error"]}')
            return m['result']
    sys.exit(f'funes mcp exited during {method}')
call(0, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                       'clientInfo': {'name': 'day0compare', 'version': '1'}})
send({'method': 'notifications/initialized'})
hit1 = hit5 = found = 0; first = None; lat = []
head = re.compile(r'^\s*→ get (\S+) --from', re.M)
for n, q in enumerate(qs, 1):
    # Results are passages: ask for 50, as the MemPalace and agentmemory
    # drivers do, from a pool of 51, since funes reranks only a pool larger
    # than the hits it returns. Then rank the sessions they belong to.
    t1 = time.time()
    r = call(n, 'tools/call', {'name': 'recall', 'arguments': {
        'query': q['question'], 'k': 50, 'candidates': 51}})
    dt = time.time() - t1; lat.append(dt)
    if first is None: first = dt
    text = ''.join(c.get('text', '') for c in r['content'])
    if r.get('isError'):
        sys.exit(f'funes recall: {text[-400:]}')
    want = set(q['answer_session_ids'])
    order = []
    for m in head.finditer(text):
        sid = m.group(1)
        if sid not in order: order.append(sid)
    rank = next((k for k, sid in enumerate(order[:50]) if sid in want), None)
    if rank is not None:
        found += 1
        if rank == 0: hit1 += 1
        if rank < 5: hit5 += 1
mcp.stdin.close()
mcp.wait()
lat.sort()
print(f'funes index {build:.0f}s first {first*1000:.0f}ms p50 {lat[len(lat)//2]*1000:.0f}ms '
      f'hit@1 {hit1}/{len(qs)} hit@5 {hit5}/{len(qs)} found@50 {found}/{len(qs)}')

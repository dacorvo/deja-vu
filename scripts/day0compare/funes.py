# Score Hugging Face funes over the day0bench corpus. funes indexes only
# .funes.jsonl turns files, which each agent's separate integration converts
# from that agent's sessions. `funes add claude` would also install the
# integration's hooks into Claude Code, and its converter needs jq, so the
# build writes those turns files itself from the Claude layout (the same
# sessions sit under both roots) and indexes them. Then every question goes
# through `funes recall`, and the answer session's id is looked for in the
# hits, in rank order. Usage:
#   FUNES=/path/to/funes python3 funes.py <keep-dir>
# The header line of a hit shortens the id to eight characters; the full one
# is on the line under it:
#   → get answer_355c48bb --from 0 --to 3 --memory …
import glob, json, os, re, shutil, subprocess, sys, time
S = sys.argv[1]
FUNES = os.environ.get('FUNES', 'funes')
home = os.path.join(S, 'home')
env = dict(os.environ, HOME=home)
corpus = os.path.join(home, '.claude', 'projects')
turns = os.path.join(S, 'funes-turns')
memory = os.path.join(home, '.funes', 'memory')
t0 = time.time()
if not os.path.exists(memory):
    srcs = glob.glob(os.path.join(corpus, '*', '*.jsonl'))
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
build = time.time() - t0
qs = json.load(open(os.path.join(S, 'questions.json')))
hit1 = hit5 = found = 0; first = None; lat = []
head = re.compile(r'^\s*→ get (\S+) --from', re.M)
for q in qs:
    # Results are passages; ask for 200 passages reranked from 500 candidates
    # and rank the first 50 distinct sessions they belong to.
    args = [FUNES, 'recall', q['question'], '-k', '200', '--candidates', '500']
    t1 = time.time()
    r = subprocess.run(args, capture_output=True, text=True, env=env)
    dt = time.time() - t1; lat.append(dt)
    if first is None: first = dt
    want = set(q['answer_session_ids'])
    order = []
    for m in head.finditer(r.stdout):
        sid = m.group(1)
        if sid not in order: order.append(sid)
    rank = next((k for k, sid in enumerate(order[:50]) if sid in want), None)
    if rank is not None:
        found += 1
        if rank == 0: hit1 += 1
        if rank < 5: hit5 += 1
lat.sort()
print(f'funes index {build:.0f}s first {first*1000:.0f}ms p50 {lat[len(lat)//2]*1000:.0f}ms '
      f'hit@1 {hit1}/{len(qs)} hit@5 {hit5}/{len(qs)} found@50 {found}/{len(qs)}')

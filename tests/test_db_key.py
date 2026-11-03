"""
Database key handling in signal_checker.py: which role a key acts as, the headers
each key format needs, the public-key warning, and that a refused skip journal is
now reported instead of skipped silently. No network.

    python tests/test_db_key.py
"""
import base64, io, contextlib, json, os, re, sys
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import signal_checker as sc


def check(cond, msg):
    if not cond:
        print('FAIL', msg); sys.exit(1)
    print('ok  ', msg)


def jwt(role):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip('=')
    return f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc({'iss': 'supabase', 'role': role})}.c2lnbmF0dXJl"


public = re.search(r"SUPABASE_ANON_KEY:\s*'([^']+)'", open(os.path.join(ROOT, 'config.js'), encoding='utf-8').read()).group(1)
check(sc._supabase_key_role(public) == 'anon', "the key published in config.js reads as role 'anon'")
check(sc._supabase_key_role(jwt('service_role')) == 'service_role', "a service_role key reads as 'service_role'")
check(sc._supabase_key_role('sb_secret_abc123') == 'secret', "new-format secret key reads as 'secret'")
check(sc._supabase_key_role('sb_publishable_abc123') == 'publishable', "new-format public key reads as 'publishable'")
check(sc._supabase_key_role('not-a-key') is None and sc._supabase_key_role('') is None, 'garbage or empty: None, no crash')

sc.SUPABASE_KEY = jwt('service_role')
h = sc._sb_headers({'Content-Type': 'application/json'})
check(h == {'apikey': sc.SUPABASE_KEY, 'Authorization': f'Bearer {sc.SUPABASE_KEY}', 'Content-Type': 'application/json'},
      'legacy JWT key: apikey + Bearer, extras merged')
sc.SUPABASE_KEY = 'sb_secret_abc123'
check(sc._sb_headers() == {'apikey': 'sb_secret_abc123'}, 'new-format key: apikey only, never sent as a Bearer token')

sent = []
sc.send_telegram = lambda m: sent.append(m)
for key, label, warns in ((public, 'public key', True), (jwt('service_role'), 'service_role key', False),
                          ('sb_secret_abc', 'new secret key', False), ('sb_publishable_abc', 'new public key', True)):
    sc.SUPABASE_KEY, sent[:] = key, []
    cache = {}
    with contextlib.redirect_stdout(io.StringIO()) as out:
        sc._check_db_key(cache)
        sc._check_db_key(cache)             # second run the same day
    if warns:
        check('PUBLIC key' in out.getvalue() and len(sent) == 1 and 'service_role' in sent[0],
              f'{label}: warns in the log every run, Telegram only once a day')
    else:
        check(not out.getvalue() and not sent, f'{label}: silent')

cache = {'_db_key_warned': {'at': sc.time.time() - 25 * 3600}}
sc.SUPABASE_KEY, sent[:] = public, []
with contextlib.redirect_stdout(io.StringIO()):
    sc._check_db_key(cache)
check(len(sent) == 1, 'and Telegrams again after 24h while still on the public key')


class R:
    def __init__(s, code, js=None): s.status_code, s.js = code, js or []
    def json(s): return s.js


sc.SUPABASE_URL, sc.SUPABASE_KEY = 'https://sb.test', public
sc.requests.get = lambda url, headers=None, timeout=None: R(401) if 'skipped_setups' in url else R(200, [])
with contextlib.redirect_stdout(io.StringIO()) as out:
    sc.grade_journal_followups()
check('skip grading unavailable (HTTP 401)' in out.getvalue(), 'a refused skip journal is reported, no longer a silent return')
with contextlib.redirect_stdout(io.StringIO()) as out:
    txt, ev = sc._skip_history_context('SOL-USDT')
check(txt == '' and 'past skips unavailable to the AI (HTTP 401)' in out.getvalue(),
      'and the AI prompt says so in the log when it gets no past skips')
print('ALL PASSED')

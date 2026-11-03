"""
Database permission check. Run on the VPS after changing SUPABASE_KEY or the table
permissions (infra/supabase-permissions.sql):

    cd C:\\OKXAI; .venv\\Scripts\\python.exe db_check.py

Checks two keys, with requests that touch no data (reads of one id; writes aimed at
an id that does not exist; an empty batch insert):

  1. The worker's key (SUPABASE_KEY in .env) must be the PRIVATE service key and must
     be able to read and write its three tables. Until 2026-11-03 it was the public
     key, which cannot touch skipped_setups at all: every AI skip was rejected.
  2. The public key (SUPABASE_ANON_KEY in config.js, published on GitHub and the
     website) should only be able to READ trades and read/save the encrypted
     dashboard settings. Anything more is something anyone on the internet can do.

Exit code 0 = the worker's key is right. The public-key section reports separately.
"""
import os, re, sys
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from ai_check import load_env

OK, DENIED = (200, 201, 204), (401, 403)


def probe(url, key, method, path, body=None):
    h = {'apikey': key, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}
    if key.count('.') == 2:
        h['Authorization'] = f'Bearer {key}'
    r = requests.request(method, f'{url}/rest/v1/{path}', headers=h, json=body, timeout=20)
    return r.status_code, r.text[:160]


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    load_env(os.path.join(ROOT, '.env'))
    import signal_checker as sc

    url = sc.SUPABASE_URL
    if not url or not sc.SUPABASE_KEY:
        print('FAIL  SUPABASE_URL / SUPABASE_KEY missing from .env'); return 1
    worker_role = sc._supabase_key_role()
    cfg = open(os.path.join(ROOT, 'config.js'), encoding='utf-8').read()
    m = re.search(r"SUPABASE_ANON_KEY:\s*'([^']+)'", cfg)
    public = m.group(1) if m else ''

    print(f'1. WORKER KEY (C:\\OKXAI\\.env) - role: {worker_role}')
    worker_ok = True
    if worker_role in ('anon', 'publishable') or (public and sc.SUPABASE_KEY == public):
        print('  FAIL  this is the PUBLIC key from config.js, not the private service_role key')
        worker_ok = False
    checks = [
        ('read trades',               'GET',   'option3_trades?select=id&limit=1', None),
        ('update trades',             'PATCH', 'option3_trades?id=eq.db-check-no-such-row', {'exit_reason': None}),
        ('read skipped setups',       'GET',   'skipped_setups?select=id&limit=1', None),
        ('save skipped setups',       'POST',  'skipped_setups', []),
        ('grade skipped setups',      'PATCH', 'skipped_setups?id=eq.-1', {'followup': None}),
        ('read learning results',     'GET',   'learned_rules?select=id&limit=1', None),
        ('save learning results',     'POST',  'learned_rules', []),
    ]
    for label, method, path, body in checks:
        code, text = probe(url, sc.SUPABASE_KEY, method, path, body)
        good = code in OK
        worker_ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {label:<24} HTTP {code}{'' if good else '  ' + text}")

    if public:
        print('\n2. PUBLIC KEY (config.js - anyone can use it)')
        expect = [
            ('read trades (dashboard)',          'GET',   'option3_trades?select=id&limit=1', None, True),
            ('read settings (dashboard)',        'GET',   'app_settings?select=id&limit=1', None, True),
            ('save settings (dashboard)',        'PATCH', 'app_settings?id=eq.db-check-no-such-row', {'iv': None}, True),
            ('edit trades',                      'PATCH', 'option3_trades?id=eq.db-check-no-such-row', {'exit_reason': None}, False),
            ('add trades',                       'POST',  'option3_trades', [], False),
            ('delete trades',                    'DELETE', 'option3_trades?id=eq.db-check-no-such-row', None, False),
            ('touch skipped setups',             'GET',   'skipped_setups?select=id&limit=1', None, False),
            ('save learning results',            'POST',  'learned_rules', [], False),
            ('delete settings',                  'DELETE', 'app_settings?id=eq.db-check-no-such-row', None, False),
        ]
        open_writes = 0
        for label, method, path, body, should in expect:
            code, _ = probe(url, public, method, path, body)
            allowed = code in OK
            fine = allowed == should
            if not fine and not should:
                open_writes += 1
            state = 'allowed' if allowed else 'blocked' if code in DENIED else f'HTTP {code}'
            print(f"  {'PASS' if fine else 'OPEN' if not should else 'FAIL'}  {label:<34} {state}"
                  f"{'' if fine else ('  <- anyone can do this' if not should else '  <- the dashboard needs this')}")
        print(f"\n  public key: {'locked down' if not open_writes else f'{open_writes} write permission(s) still open to anyone'}")

    print(f"\n{'PASS' if worker_ok else 'FAIL'} - worker key")
    return 0 if worker_ok else 1


if __name__ == '__main__':
    sys.exit(main())

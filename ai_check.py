"""
Pre-flight check for the worker's AI calls. Run on the VPS after changing
CLAUDE_MODEL or CLAUDE_EFFORT in signal_checker.py:

    cd C:\\OKXAI; .venv\\Scripts\\python.exe ai_check.py

Why this exists: when the AI call fails - a model the key cannot use, a
parameter the model rejects - the worker does not crash. It logs the error and
treats that trade as a SKIP, so a broken model switch looks exactly like a quiet
market: no trades, no alarm. This makes two real calls through the same
claude_request() the worker uses (same model, effort, thinking, refusal fallback,
headers), with the key from C:\\OKXAI\\.env, and says plainly whether they work:

  1. the trade-advisor shape - one line back, read by the same TRADE/SKIP parser
  2. the learning-pass shape - structured JSON output (learn.py)

Cost: two short calls, about one US cent. Places no orders, touches no OKX keys.
Exit code 0 = both passed.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path):
    """Same rules as infra/run-okx.ps1: KEY=VALUE lines, optional quotes. Must run
    before signal_checker is imported, which reads os.environ at import time."""
    if not os.path.exists(path):
        return False
    with open(path, encoding='utf-8-sig') as f:
        for line in f:
            m = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
            if not m:
                continue
            val = m.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in '"\'':
                val = val[1:-1]
            os.environ.setdefault(m.group(1), val)
    return True


def attempt(name, fn):
    t0 = time.time()
    try:
        ok, detail, info = fn()
    except Exception as e:
        body = getattr(getattr(e, 'response', None), 'text', '') or ''
        print(f'  FAIL  {name}: {e}')
        if body:
            print(f'        API said: {body[:400]}')
        return False
    secs = time.time() - t0
    u = info.get('usage') or {}
    tin, tout = u.get('input_tokens', 0), u.get('output_tokens', 0)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")
    via = ' (via refusal fallback)' if info.get('fallback') else ''
    print(f"        answered by {info['model']}{via}, stop_reason {info['stop_reason']}, "
          f"{secs:.1f}s, {tin} in / {tout} out tokens")
    return ok


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    had_env = load_env(os.path.join(HERE, '.env'))
    sys.path.insert(0, HERE)
    import signal_checker as sc

    print(f'model {sc.CLAUDE_MODEL} | effort {sc.CLAUDE_EFFORT} | fallback beta {sc.CLAUDE_FALLBACK_BETA}')
    if not sc.CLAUDE_API_KEY:
        print(f"FAIL  CLAUDE_API_KEY is empty ({'not in' if had_env else 'no'} .env at {HERE}).")
        return 1

    def advisor_shape():
        text, info = sc.claude_request(
            'You are a connectivity test for a trading bot. Reply with exactly one line and nothing else.',
            'Reply with exactly: [SKIP: connectivity test]',
            max_tokens=16000, timeout=120)
        if info['refusal']:
            return False, f"declined (refusal: {info['refusal']})", info
        # The same pattern ai_trade_params uses to read a SKIP.
        m = re.search(r'\[SKIP[:\s]*(.*?)\]', text, re.IGNORECASE)
        ok = bool(m) and 'connectivity' in m.group(1).lower()
        return ok, (f'parser read SKIP "{m.group(1)}"' if ok else f'unexpected reply: {text[:120]!r}'), info

    def learning_shape():
        schema = {'type': 'object', 'additionalProperties': False,
                  'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok']}
        text, info = sc.claude_request(
            'You are a connectivity test. Return the requested JSON.',
            'Return {"ok": true}.',
            max_tokens=8000, timeout=120,
            output_format={'type': 'json_schema', 'schema': schema})
        if info['refusal']:
            return False, f"declined (refusal: {info['refusal']})", info
        try:
            ok = json.loads(text).get('ok') is True
        except ValueError:
            ok = False
        return ok, ('structured JSON parsed' if ok else f'unexpected reply: {text[:120]!r}'), info

    results = [attempt('trade advisor call', advisor_shape),
               attempt('learning pass call', learning_shape)]
    if all(results):
        print('\nPASS - the worker can use this model. Nothing else to do; it reloads '
              'signal_checker.py on its next ~4-minute relaunch.')
        return 0
    print('\nFAIL - until this passes, every trade decision is logged as a skip. '
          'Revert CLAUDE_MODEL / CLAUDE_EFFORT in signal_checker.py or fix what the API said above.')
    return 1


if __name__ == '__main__':
    sys.exit(main())

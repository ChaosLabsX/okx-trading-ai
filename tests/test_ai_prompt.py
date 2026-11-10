"""
The trade advisor's prompt still renders and its answer is still parsed. The system
prompt is one large f-string: a stray brace or a renamed constant inside it raises at
call time, which the worker catches and logs as a SKIP — every trade would quietly
stop. Run after editing ai_trade_params or any constant its prompt interpolates.

    python tests/test_ai_prompt.py
"""
import io, contextlib, os, sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import signal_checker as sc

sc.CLAUDE_API_KEY = 'k'
sent = {}


class R:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return {'content': [{'type': 'thinking', 'thinking': ''}, {'type': 'text', 'text': '[SKIP: test]'}],
                            'stop_reason': 'end_turn', 'model': sc.CLAUDE_MODEL, 'usage': {}}


def post(url, headers=None, json=None, timeout=None):
    sent.update(json); return R()


sc.requests.post = post
sc._trade_history_context = lambda s: ('Overall: 1 closed trade\n  - TRX be_stop +0.12', None, {'journal_n': 1, 'journal_win_rate': 1.0})
sc._skip_history_context = lambda s: ('Last 3 graded skips: 1 correctly avoided a loss, 2 would have hit target', {})
with contextlib.redirect_stdout(io.StringIO()):
    p, why = sc.ai_trade_params('SOL-USDT', {'score': 4.0, 'reasons': ['MACD bullish crossover', 'Volume 2.4x avg']},
                                {'price': 150.0, 'change_pct': -2.0}, 280.0, 33.0, 41.0,
                                {'bullish_cross': True, 'bearish_cross': False, 'trend': 'bullish'}, {'pct_b': 0.15}, 2.4,
                                extra={'atr_pct': 1.1, 'equity': 400.0, 'oi_chg_24h': -17.3, 'ls_ratio': 2.41,
                                       'taker_buy': 0.46, 'breadth_os': 0.35}, evidence_out={})
s, u = sent['system'], sent['messages'][0]['content']
assert why == 'test' and p is None, (p, why)
assert f'Score {sc.STRONG_BUY_SCORE:g}–4.9' in s, 'sizing band for the lowest score'
assert 'a scratch (about $0)' in s and 'be_stop' in s, 'break-even exit definitions'
assert 'YOUR PAST SKIPS' in u and 'would have hit target' in u, 'past skips reach the prompt when the journal has them'
assert sent['model'] == sc.CLAUDE_MODEL and sent['output_config']['effort'] == sc.CLAUDE_EFFORT
print('ok   prompt renders: sizing band, exit definitions, past skips, model and effort; SKIP parsed')
assert 'RISK CAP' in s and '($400.00)' in s and '$133 at a 3% stop' in s and '$50 at 8%' in s, 'risk cap, on the whole account'
assert 'open interest, 24h change (in coins): -17.3%' in u and 'long/short ratio: 2.41' in u \
       and '46% buying' in u and '35% of scanned coins are oversold' in u, 'futures flow and breadth lines'
assert 'Open interest:' not in u, 'the bare open-interest level is gone (a level without a change says nothing)'
print('ok   and carries the risk cap, the 24h open-interest change, long/short, taker flow and breadth')

# The size the AI asks for is cut to the risk cap, and a cut below $10 is a skip, not a trade.
answer = {'text': '[TRADE:{"side":"buy","symbol":"SOL-USDT","amountUsdt":170,"partialTpPct":3,"trailingCallbackPct":1.2,"slPct":4}]'}
R.json = lambda self: {'content': [{'type': 'text', 'text': answer['text']}], 'stop_reason': 'end_turn',
                       'model': sc.CLAUDE_MODEL, 'usage': {}}
args = ('SOL-USDT', {'score': 4.5, 'reasons': ['a', 'b']}, {'price': 150.0, 'change_pct': -2.0}, 380.0, 28.0, 38.0,
        {'bullish_cross': False, 'bearish_cross': False, 'trend': 'bearish'}, {'pct_b': 0.1}, 2.4)
ev = {}
with contextlib.redirect_stdout(io.StringIO()):
    p, why = sc.ai_trade_params(*args, extra={'equity': 400.0}, evidence_out=ev)
assert p and p['amount_usdt'] == 100.0 and p['sl_pct'] == 4.0 and ev.get('risk_capped'), (p, ev)
with contextlib.redirect_stdout(io.StringIO()):
    p, why = sc.ai_trade_params(*args, extra={'equity': 30.0}, evidence_out={})
assert p is None and 'risk cap' in why, (p, why)
old = sc.RISK_PER_TRADE_PCT
sc.RISK_PER_TRADE_PCT = 0
with contextlib.redirect_stdout(io.StringIO()):
    p, why = sc.ai_trade_params(*args, extra={'equity': 400.0}, evidence_out={})
sc.RISK_PER_TRADE_PCT = old
assert p and p['amount_usdt'] == 170.0, p
print('ok   $170 asked at a 4% stop on a $400 account -> $100 (1% at risk); tiny account -> skip; 0 turns the cap off')
print('ALL PASSED')

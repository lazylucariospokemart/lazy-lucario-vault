"""Shared helpers for the singles price file prices/latest.json.

The file maps "<tcgplayer id>|<condition>" to {price, chg7, printing, language, at, src}.
Sources: "card-vault" (a scanner CSV dropped in incoming/), "justtcg" (daily GitHub job with
a JustTCG key, condition-specific) or "tcgplayer" (daily GitHub job without a key: Near Mint
market price scaled by condition).
"""
import json, os, datetime

PRICES = 'prices/latest.json'
HISTORY_DIR = 'prices/history'
COND_FULL = {'NM': 'Near Mint', 'LP': 'Lightly Played', 'MP': 'Moderately Played', 'HP': 'Heavily Played', 'DMG': 'Damaged'}
COND_ABBR = {v: k for k, v in COND_FULL.items()}
# fallback when only a Near Mint market price is known (TCGplayer public price)
COND_FACTOR = {'NM': 1.0, 'LP': 0.85, 'MP': 0.70, 'HP': 0.55, 'DMG': 0.40}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def cond_abbr(c):
    c = (c or 'NM').strip()
    if c in COND_FULL:
        return c
    return COND_ABBR.get(c, 'NM')


def key(tcg_id, cond):
    return f'{tcg_id}|{cond_abbr(cond)}'


def norm_printing(p):
    return (p or 'Normal').replace(' - Japanese', '').strip() or 'Normal'


def load_prices():
    if os.path.exists(PRICES):
        try:
            return json.load(open(PRICES))
        except Exception:
            pass
    return {}


def save_prices(d):
    os.makedirs(os.path.dirname(PRICES), exist_ok=True)
    json.dump(dict(sorted(d.items())), open(PRICES, 'w'), indent=1, sort_keys=True)


def record_history(d):
    """Keep one snapshot per day so a 7-day change can be worked out when the source gives none."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    snap = {k: v.get('price') for k, v in d.items() if v.get('price') is not None}
    json.dump(snap, open(os.path.join(HISTORY_DIR, f'{today}.json'), 'w'), separators=(',', ':'))
    # keep 60 days
    files = sorted(os.listdir(HISTORY_DIR))
    for f in files[:-60]:
        os.remove(os.path.join(HISTORY_DIR, f))


def chg7_from_history(k, price):
    """Percent change vs the snapshot closest to 7 days ago, or None."""
    if price is None or not os.path.isdir(HISTORY_DIR):
        return None
    target = datetime.date.today() - datetime.timedelta(days=7)
    best, best_gap = None, 99
    for f in os.listdir(HISTORY_DIR):
        try:
            d = datetime.date.fromisoformat(f[:-5])
        except ValueError:
            continue
        gap = abs((d - target).days)
        if gap <= 2 and gap < best_gap:
            best, best_gap = f, gap
    if not best:
        return None
    try:
        old = json.load(open(os.path.join(HISTORY_DIR, best))).get(k)
    except Exception:
        return None
    if not old:
        return None
    return round((price - old) / old * 100, 2)


def singles_from_export(export_dir='export/cards'):
    """{doc_id: card} for unsold singles that carry a TCGplayer id."""
    import glob
    out = {}
    for f in glob.glob(os.path.join(export_dir, '*.json')):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        d = d.get('data', d)
        if d.get('kind') or d.get('sold'):
            continue
        tid = str(d.get('img') or '').strip()
        if tid.isdigit():
            out[os.path.basename(f)[:-5]] = d
    return out

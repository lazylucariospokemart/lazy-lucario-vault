"""Refresh prices/latest.json for every unsold single on the storefront (runs on GitHub Actions).

With a JUSTTCG_KEY environment variable: condition-specific market prices and 7-day change from
JustTCG (the same source Card Vault uses). Without one: TCGplayer's public Near Mint market price
per printing, scaled by condition, with the 7-day change worked out from this folder's own history.
Never touches the Owner desk; the Vault Publisher applies the file to it on its next run.
Usage: python3 -I fetch_prices.py
"""
import json, os, sys, time, datetime, urllib.request, urllib.parse
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I mode: find vault_prices next to this script

from vault_prices import (load_prices, save_prices, record_history, chg7_from_history, key,
                          cond_abbr, COND_FULL, COND_FACTOR, norm_printing, now_iso, singles_from_export)

UA = {'User-Agent': 'Mozilla/5.0 (compatible; LazyLucarioVault/1.0)', 'Accept': 'application/json'}
JUSTTCG = 'https://api.justtcg.com/v1'
KEY = os.environ.get('JUSTTCG_KEY', '').strip()


def http(url, body=None, headers=None):
    h = dict(UA); h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode(); h['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode())


def pick_variant(variants, cond, printing, language):
    """Mirror of Card Vault's pick_price: same condition + language, exact printing else loose."""
    full = COND_FULL.get(cond_abbr(cond), 'Near Mint'); lang = (language or 'English').lower()
    cands = [v for v in variants if v.get('condition') == full and (v.get('language') or 'English').lower() == lang]
    if printing:
        exact = [v for v in cands if (v.get('printing') or '').lower() == printing.lower()]
        cands = exact or [v for v in cands if printing.lower() in (v.get('printing') or '').lower()]
    if not cands:
        return None, None
    v = cands[0]
    price = v.get('price') if v.get('price') is not None else v.get('lastKnownPrice')
    return price, v.get('priceChange7d')


def via_justtcg(combos):
    """combos: {tcgId: [(cond, printing, language)]} -> {key: (price, chg7)}"""
    out, ids = {}, list(combos)
    hdr = {'x-api-key': KEY}
    for i in range(0, len(ids), 20):
        chunk = ids[i:i + 20]
        cards = []
        try:
            data = http(f'{JUSTTCG}/cards', body=[{'tcgplayerId': t} for t in chunk], headers=hdr)
            cards = data.get('data') or []
        except Exception as e:
            print(f'  batch failed ({e}); trying one by one')
            for t in chunk:
                try:
                    d = http(f'{JUSTTCG}/cards?' + urllib.parse.urlencode({'tcgplayerId': t, 'include_statistics': '7d'}), headers=hdr)
                    cards += d.get('data') or []
                except Exception as e2:
                    print(f'  {t}: {e2}')
                time.sleep(6.5)  # free plan: 10 requests a minute
        by_id = {str(c.get('tcgplayerId')): c for c in cards if c.get('tcgplayerId') is not None}
        for t in chunk:
            c = by_id.get(t)
            if not c:
                continue
            for cond, printing, language in combos[t]:
                price, chg = pick_variant(c.get('variants') or [], cond, printing, language)
                if price is not None:
                    out[key(t, cond)] = (round(float(price), 2), chg)
        time.sleep(6.5)
    return out


def via_tcgplayer(combos):
    out = {}
    for t, lst in combos.items():
        try:
            pts = http(f'https://mpapi.tcgplayer.com/v2/product/{t}/pricepoints')
        except Exception as e:
            print(f'  {t}: {e}'); continue
        if isinstance(pts, dict):
            pts = pts.get('result') or pts.get('data') or []
        if t in os.environ.get('DEBUG_IDS', '').split(','):
            print(f'  DEBUG {t}: {json.dumps(pts)[:1500]}')
        for cond, printing, language in lst:
            # TCGplayer's public feed only knows "Normal" and "Foil": any holo/foil printing is Foil
            want = 'foil' if any(w in norm_printing(printing).lower() for w in ('holo', 'foil')) else 'normal'
            cand = [p for p in pts if isinstance(p.get('marketPrice'), (int, float)) and p['marketPrice'] > 0]
            hit = next((p for p in cand if (p.get('printingType') or '').lower() == want), None) or (cand[0] if cand else None)
            if hit:
                price = round(hit['marketPrice'] * COND_FACTOR.get(cond_abbr(cond), 1.0), 2)
                out[key(t, cond)] = (price, None)
        time.sleep(0.3)
    return out


if __name__ == '__main__':
    singles = singles_from_export()
    combos = {}
    for d in singles.values():
        combos.setdefault(str(d['img']), []).append((cond_abbr(d.get('cond')), norm_printing(d.get('printing')), d.get('language') or 'English'))
    for t in combos:
        combos[t] = sorted(set(combos[t]))
    print(f'{len(singles)} unsold singles, {len(combos)} products, source {"justtcg" if KEY else "tcgplayer"}')
    prices = load_prices()
    found = via_justtcg(combos) if KEY else via_tcgplayer(combos)
    src = 'justtcg' if KEY else 'tcgplayer'
    at = now_iso(); updated = 0
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).isoformat()
    for k, (price, chg) in found.items():
        tid, cond = k.split('|')
        old = prices.get(k, {})
        # the public feed has no condition data: never let it overwrite a Card Vault / JustTCG price under a week old
        if src == 'tcgplayer' and old.get('src') in ('card-vault', 'justtcg') and (old.get('at') or '') > cutoff:
            continue
        printing = next((p for c, p, l in combos.get(tid, []) if c == cond), 'Normal')
        if chg is None:
            chg = chg7_from_history(k, price)
        old = prices.get(k, {})
        if old.get('price') != price or old.get('chg7') != chg:
            updated += 1
        prices[k] = {'price': price, 'chg7': chg, 'printing': printing, 'at': at, 'src': src}
    save_prices(prices)
    record_history(prices)
    print(f'done: {len(found)} priced, {updated} changed, {len(singles) - sum(1 for d in singles.values() if key(d["img"], d.get("cond")) in found)} without a price')

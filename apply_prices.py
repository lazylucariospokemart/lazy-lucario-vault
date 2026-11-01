"""Work out which Owner desk cards need a new market price (run by the Vault Publisher after the export).

Reads export/cards (the fresh export) and prices/latest.json and writes /tmp/price_updates.json:
a list of {"doc_id", "price", "chg7", "priceAt"} for unsold singles whose price in the file differs
from the desk. The Publisher then fetches versions and sends them as ArtifactData "update" writes.
Only price, chg7 and priceAt ever change; asking prices (ask), notes, qty and sold are never touched.
Usage: python3 -I apply_prices.py
"""
import json
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I mode: find vault_prices next to this script

from vault_prices import load_prices, key, singles_from_export

OUT = '/tmp/price_updates.json'

if __name__ == '__main__':
    prices = load_prices()
    singles = singles_from_export()
    updates = []
    for doc_id, d in singles.items():
        p = prices.get(key(d['img'], d.get('cond')))
        if not p or p.get('price') is None:
            continue
        price, chg = round(float(p['price']), 2), p.get('chg7')
        old = d.get('price')
        same_price = old is not None and abs(float(old) - price) < 0.005
        same_chg = (d.get('chg7') is None and chg is None) or (d.get('chg7') is not None and chg is not None and abs(float(d['chg7']) - float(chg)) < 0.005)
        if same_price and same_chg:
            continue
        updates.append({'doc_id': doc_id, 'price': price, 'chg7': chg, 'priceAt': p.get('at')})
    json.dump(updates, open(OUT, 'w'))
    print(f'{len(updates)} cards need a price update (of {len(singles)} unsold singles, {len(prices)} prices on file)')

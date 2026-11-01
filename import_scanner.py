"""Turn scanner CSV exports in incoming/ into a batch of new Owner desk cards.
Usage: python3 -I import_scanner.py   (run after the fresh export/ step)
Writes /tmp/scanner_writes.json (a list of ArtifactData batch 'set' writes, max 50 per call)
and prints how many new cards were found. Cards already listed (same TCGplayer id) are skipped
as new cards, but their market_price from the CSV is recorded in prices/latest.json so the
Publisher's price step refreshes them (Card Vault prices are condition-specific).
"""
import csv, glob, json, os, datetime
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I mode: find vault_prices next to this script

from vault_prices import load_prices, save_prices, key, now_iso, norm_printing
COND = {'Near Mint':'NM','Lightly Played':'LP','Moderately Played':'MP','Heavily Played':'HP','Damaged':'DMG'}
prices, priced = load_prices(), 0
have, ids = set(), []
for f in glob.glob('export/cards/*.json'):
    d = json.load(open(f)); d = d.get('data', d)
    if d.get('img'): have.add(str(d['img']))
    n = os.path.basename(f)[:-5]
    if n.isdigit() and int(n) < 900000000: ids.append(int(n))
nxt = max(ids or [0]) + 1
today = datetime.date.today().isoformat()
writes, files = [], sorted(glob.glob('incoming/*.csv'))
for fn in files:
    for r in csv.DictReader(open(fn, encoding='utf-8-sig')):
        tid = (r.get('tcgplayer_id') or '').strip()
        if not tid: continue
        try: price = round(float(r['market_price']), 2)
        except (ValueError, TypeError, KeyError): price = None
        if price is not None:  # every row carries Card Vault's current price for that condition
            prices[key(tid, COND.get(r.get('condition',''),'NM'))] = {'price': price, 'chg7': None, 'printing': norm_printing(r.get('printing','')), 'at': now_iso(), 'src': 'card-vault'}
            priced += 1
        if tid in have: continue
        have.add(tid)
        writes.append({'op':'set','collection':'cards','doc_id':str(nxt),'data':{
            'added':today,'chg7':None,'cond':COND.get(r.get('condition',''),'NM'),'img':tid,
            'language':r.get('language',''),'name':r.get('name',''),'notes':r.get('notes',''),
            'number':r.get('number',''),'price':price,'printing':r.get('printing',''),
            'qty':int(r.get('quantity') or 1),'rarity':r.get('rarity',''),'set':r.get('set_name',''),'sold':False}})
        nxt += 1
json.dump(writes, open('/tmp/scanner_writes.json','w'))
if priced: save_prices(prices)
print(f'{len(writes)} new cards from {len(files)} file(s); {priced} prices recorded')

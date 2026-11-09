"""Build the static public site from the exported artifact db + page source."""
import json, os, glob, shutil, re, datetime
import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I mode: find sales_log next to this script
import sales_log
SRC='src/page.html'; OUT='.'
os.makedirs(OUT,exist_ok=True)
cards={}
for f in glob.glob('export/cards/*.json'):
    cards[os.path.splitext(os.path.basename(f))[0]]=json.load(open(f))
# latest market prices for unsold singles (prices/latest.json, keyed "<tcgplayer id>|<condition>"); the Owner desk catches up on the Publisher's next run
if os.path.exists('prices/latest.json'):
    try: _p=json.load(open('prices/latest.json'))
    except Exception: _p={}
    for c in cards.values():
        if c.get('kind') or c.get('sold') or not c.get('img'): continue
        e=_p.get(f"{c['img']}|{c.get('cond') or 'NM'}")
        if e and e.get('price') is not None:
            c['price']=round(float(e['price']),2); c['chg7']=e.get('chg7')
meta=json.load(open('export/meta/site.json')) if os.path.exists('export/meta/site.json') else {}
meta['updated']=datetime.date.today().isoformat()
meta.pop('orderHook',None)  # never publish a raw Discord webhook
applied=[]
if os.path.exists('export/meta/orders.json'):
    applied=json.load(open('export/meta/orders.json')).get('applied',[])
page=open(SRC).read()
# strip the artifact-only meta tag
page=page.replace('<meta name="claude:viewport-fit-sides" content="cover">\n','')
_a='${c.sold?\'<div class="stamp">SOLD</div>\':\'\'}'
if _a in page: page=page.replace(_a,_a+'${c._new?\'<div class="newtag">NEW</div>\':\'\'}',1)
else: print('warning: NEW tag anchor not found in page')
page=page.replace('</style>','.newtag{position:absolute;top:9px;left:9px;z-index:3;background:linear-gradient(135deg,#6ee7f9,#a78bfa);color:#0b1020;font-weight:900;font-size:14px;line-height:1;letter-spacing:.09em;padding:6px 12px;border-radius:999px;border:2px solid #fff;box-shadow:0 4px 12px rgba(0,0,0,.45),0 0 0 1px rgba(167,139,250,.5);transform:rotate(-6deg);pointer-events:none;animation:newpop .5s cubic-bezier(.2,1.6,.4,1) both}@keyframes newpop{from{transform:rotate(-6deg) scale(.4);opacity:0}to{transform:rotate(-6deg) scale(1);opacity:1}}@media (prefers-reduced-motion:reduce){.newtag{animation:none}}</style>',1)
title_m=re.search(r'<title>(.*?)</title>',page)
# sold listings fall off the public page after HIDE_SOLD_DAYS (they stay on the Owner desk, in data.json and in sales.json).
# Age = soldAt if Lazy set one, else the first build that saw the card as sold (tracked in sold_seen.json).
HIDE_SOLD_DAYS=7
today=datetime.date.today()
try: seen=json.load(open('sold_seen.json'))
except Exception: seen={}
seen={k:v for k,v in seen.items() if k in cards and cards[k].get('sold')}
public={}
for k,c in cards.items():
    if not c.get('sold'): public[k]=c; continue
    d=seen.setdefault(k,today.isoformat())
    if c.get('soldAt'): d=str(c['soldAt'])[:10]
    try: age=(today-datetime.date.fromisoformat(d)).days
    except Exception: age=0
    if age<=HIDE_SOLD_DAYS: public[k]=c
# NEW tag: unsold listings added in the last NEW_DAYS days (the page code below draws the badge from _new)
NEW_DAYS=7
for k,c in public.items():
    try: c['_new']=(not c.get('sold')) and 0<=(today-datetime.date.fromisoformat(str(c.get('added'))[:10])).days<=NEW_DAYS
    except Exception: c['_new']=False
json.dump(seen,open('sold_seen.json','w'),indent=0,sort_keys=True)
print(f"public page: {len(public)} of {len(cards)} cards ({len(cards)-len(public)} sold listings aged off)")
data=json.dumps({'cards':public,'meta':meta},separators=(',',':'))
# data.json is what the order relay reads to check stock and prices
json.dump({'cards':cards,'meta':meta,'appliedOrders':applied,'builtAt':datetime.datetime.now(datetime.timezone.utc).isoformat()},open(f'{OUT}/data.json','w'),separators=(',',':'))
html=('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
      f'<meta name="description" content="{title_m.group(1)} — Pokémon singles for sale, priced to condition, Venmo + tracked shipping.">'
      '<link rel="icon" href="favicon.svg">'
      '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>'
      f'<script>window.VAULT_DATA={data};</script></head><body>'+page+'</body></html>')
open(f'{OUT}/index.html','w').write(html)
open(f'{OUT}/favicon.svg','w').write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 44"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6ee7f9"/><stop offset=".5" stop-color="#a78bfa"/><stop offset="1" stop-color="#fde68a"/></linearGradient></defs><rect width="32" height="44" rx="4" fill="url(#g)"/><rect x="3" y="3" width="26" height="38" rx="2" fill="none" stroke="#fff" stroke-opacity=".7" stroke-width="2"/></svg>')
open(f'{OUT}/.nojekyll','w').write('')
print(f"built {OUT}/index.html with {len(cards)} cards, {len(os.listdir(OUT+'/img'))} images, {os.path.getsize(OUT+'/index.html')//1024} KB")
# sales.json: the realized-sales log the Foil Ledger API serves as /v1/sold (never fails the build)
try:
    _s,_a,_r=sales_log.update(cards)
    print(f"sales.json: {len(_s)} sales ({_a} added, {_r} removed)")
except Exception as e:
    print(f"sales.json skipped: {e}")

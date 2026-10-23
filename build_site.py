"""Build the static public site from the exported artifact db + page source."""
import json, os, glob, shutil, re, datetime
SRC='src/page.html'; OUT='.'
os.makedirs(OUT,exist_ok=True)
cards={}
for f in glob.glob('export/cards/*.json'):
    cards[os.path.splitext(os.path.basename(f))[0]]=json.load(open(f))
meta=json.load(open('export/meta/site.json')) if os.path.exists('export/meta/site.json') else {}
meta['updated']=datetime.date.today().isoformat()
page=open(SRC).read()
# strip the artifact-only meta tag
page=page.replace('<meta name="claude:viewport-fit-sides" content="cover">\n','')
title_m=re.search(r'<title>(.*?)</title>',page)
data=json.dumps({'cards':cards,'meta':meta},separators=(',',':'))
html=('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
      f'<meta name="description" content="{title_m.group(1)} — Pokémon singles for sale, priced to condition, Venmo + tracked shipping.">'
      '<link rel="icon" href="favicon.svg">'
      '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>'
      f'<script>window.VAULT_DATA={data};</script></head><body>'+page+'</body></html>')
open(f'{OUT}/index.html','w').write(html)
open(f'{OUT}/favicon.svg','w').write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 44"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6ee7f9"/><stop offset=".5" stop-color="#a78bfa"/><stop offset="1" stop-color="#fde68a"/></linearGradient></defs><rect width="32" height="44" rx="4" fill="url(#g)"/><rect x="3" y="3" width="26" height="38" rx="2" fill="none" stroke="#fff" stroke-opacity=".7" stroke-width="2"/></svg>')
open(f'{OUT}/.nojekyll','w').write('')
print(f"built {OUT}/index.html with {len(cards)} cards, {len(os.listdir(OUT+'/img'))} images, {os.path.getsize(OUT+'/index.html')//1024} KB")

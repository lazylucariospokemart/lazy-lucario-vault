"""Anonymous visit + cart counting for the public Vault page (GoatCounter, no cookies).

Set your GoatCounter site code in analytics.json ({"goatcounter": "mycode"} for
mycode.goatcounter.com). Empty code = nothing is added to the page.

What gets counted (numbers only, never who):
  page visits            - automatic
  add-to-cart            - someone put a card in their cart
  cart-opened            - someone opened the cart
  visit-with-cart        - someone arrived with cards already saved in their cart
  order-sent             - an order went through
Your own visits: open the site once with #notrack on the end of the address to stop
counting that browser (#track turns it back on).
"""
import json, os, re

JS = r"""<script>
(function(){
  var KEY='vault-notrack';
  try{
    if(location.hash==='#notrack')localStorage.setItem(KEY,'1');
    if(location.hash==='#track')localStorage.removeItem(KEY);
    if(localStorage.getItem(KEY))return;
  }catch(e){}
  var queue=[];
  function ev(name){
    if(window.goatcounter&&window.goatcounter.count){try{window.goatcounter.count({path:name,title:name,event:true})}catch(e){}}
    else queue.push(name);
  }
  var s=document.createElement('script');
  s.async=true; s.src='https://gc.zgo.at/count.js';
  s.setAttribute('data-goatcounter','https://__CODE__.goatcounter.com/count');
  s.onload=function(){var q=queue;queue=[];q.forEach(ev)};
  document.head.appendChild(s);
  try{var c=JSON.parse(localStorage.getItem('vault-cart')||'{}')||{};if(Object.keys(c).length)ev('visit-with-cart')}catch(e){}
  document.addEventListener('click',function(e){
    var t=e.target; if(!t||!t.closest)return;
    if(t.closest('[data-add],#addCart,#offerAdd'))ev('add-to-cart');
    else if(t.closest('#cartBtn,#goCart'))ev('cart-opened');
  },true);
  var f=window.fetch;
  if(f)window.fetch=function(u,o){
    var p=f.apply(this,arguments);
    try{if(o&&o.method==='POST'&&/\/order$/.test(String(u)))p.then(function(r){if(r&&r.ok)ev('order-sent')}).catch(function(){})}catch(e){}
    return p;
  };
})();
</script>"""

def snippet(path='analytics.json'):
    try: code=str(json.load(open(path)).get('goatcounter') or '').strip().lower()
    except Exception: return ''
    code=re.sub(r'^https?://','',code).split('.')[0]
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*',code): return ''
    return JS.replace('__CODE__',code)

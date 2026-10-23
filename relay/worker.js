// Vault Orders relay — Cloudflare Worker for the Lazy Lucario Vault storefront.
// Secrets (set in Cloudflare, never in this file): GITHUB_TOKEN, DISCORD_WEBHOOK. KV binding: OFFERS
// GET  /holds  -> {held:{cardId:qty}}  cards on hold from orders not yet applied to the Owner desk
// POST /order  -> checks stock + prices server-side, records the hold in holds.json, pings Discord
// POST /offer  -> buyer offers up to 10% under list; pings Discord with signed Accept/Decline links
// GET  /offer/<id> -> offer status for the buyer's page;  GET/POST /offer/<id>/(accept|decline)?sig= -> Lazy decides
const OWNER = 'lazylucariospokemart', REPO = 'lazy-lucario-vault';
const ORIGINS = ['https://lazylucariospokemart.github.io'];
const MAX_ORDERS_PER_IP_HOUR = 5;
const MAX_OFFERS_PER_IP_HOUR = 10;
const OFFER_MAX_PCT = 10;           // lowest offer = list price minus this %
const OFFER_PENDING_MS = 72 * 3600e3; // undecided offers expire after 3 days
const OFFER_CHECKOUT_MS = 48 * 3600e3; // accepted offers can be checked out for 2 days
const offerHits = new Map();
const hits = new Map(); // ip -> [timestamps] (per worker instance, best effort)
let holdsCache = { at: 0, held: null };

const cors = (origin) => ({
  'Access-Control-Allow-Origin': ORIGINS.includes(origin) ? origin : ORIGINS[0],
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Vary': 'Origin',
});
const json = (obj, status, origin) => new Response(JSON.stringify(obj), {
  status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...cors(origin) },
});

function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${OWNER}/${REPO}/contents/${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'User-Agent': 'vault-orders-relay',
      'X-GitHub-Api-Version': '2022-11-28',
      ...(init.headers || {}),
    },
  });
}
function b64decode(s) {
  const bin = atob(s.replace(/\n/g, ''));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}
function b64encode(str) {
  let bin = '';
  new TextEncoder().encode(str).forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin);
}
async function readJson(env, path) {
  const r = await gh(env, path + '?ref=main');
  if (r.status === 404) return { data: null, sha: null };
  if (!r.ok) throw new Error(`GitHub read ${path}: ${r.status}`);
  const j = await r.json();
  return { data: JSON.parse(b64decode(j.content)), sha: j.sha };
}
async function writeJson(env, path, data, sha, message) {
  const body = { message, content: b64encode(JSON.stringify(data, null, 1)), branch: 'main',
    committer: { name: 'Vault Orders', email: 'bfinney28@gmail.com' } };
  if (sha) body.sha = sha;
  return gh(env, path, { method: 'PUT', body: JSON.stringify(body) });
}

function activeHeld(holdsDoc, applied) {
  const held = {};
  for (const h of (holdsDoc && holdsDoc.holds) || []) {
    if (applied.has(h.order)) continue;
    for (const [id, q] of Object.entries(h.ids || {})) held[id] = (held[id] || 0) + q;
  }
  return held;
}
async function loadState(env) {
  const [site, holds] = await Promise.all([readJson(env, 'data.json'), readJson(env, 'holds.json')]);
  if (!site.data) throw new Error('data.json missing');
  const applied = new Set(site.data.appliedOrders || []);
  const holdsDoc = holds.data || { holds: [] };
  return { site: site.data, holdsDoc, sha: holds.sha, applied, held: activeHeld(holdsDoc, applied) };
}

const clean = (v, n) => String(v ?? '').replace(/[\u0000-\u001f]/g, ' ').trim().slice(0, n);
const noLinks = (s) => s.replace(/(https?:\/\/|www\.)\S+/gi, '[link removed]').replace(/\S+\.\S+\/\S*/g, '[link removed]');
const money = (n) => '$' + n.toFixed(2);

async function handleOrder(req, env, origin) {
  const ip = req.headers.get('CF-Connecting-IP') || 'x';
  const now = Date.now();
  const recent = (hits.get(ip) || []).filter((t) => now - t < 3600e3);
  if (recent.length >= MAX_ORDERS_PER_IP_HOUR) return json({ error: 'rate_limited' }, 429, origin);

  const raw = await req.text();
  if (raw.length > 8000) return json({ error: 'too_large' }, 413, origin);
  let b; try { b = JSON.parse(raw); } catch { return json({ error: 'bad_json' }, 400, origin); }

  const f = {
    name: clean(b.name, 80), addr1: clean(b.addr1, 120), addr2: clean(b.addr2, 80), city: clean(b.city, 60),
    state: clean(b.state, 30), zip: clean(b.zip, 12), paidName: clean(b.paidName, 80), contact: clean(b.contact, 100),
    app: b.app === 'Cash App' ? 'Cash App' : 'Venmo',
  };
  for (const k of ['name', 'addr1', 'city', 'state', 'zip', 'paidName', 'contact'])
    if (!f[k]) return json({ error: 'missing', field: k }, 400, origin);
  const items = {};
  for (const [id, q] of Object.entries(b.items || {})) {
    const n = Math.floor(Number(q));
    if (/^\d{1,6}$/.test(id) && n >= 1 && n <= 50) items[id] = n;
  }
  if (!Object.keys(items).length) return json({ error: 'empty' }, 400, origin);
  // accepted offers the buyer is using: {cardId: offerId}
  const offerUse = {};
  for (const [cid, oid] of Object.entries(b.offers || {})) {
    if (!items[cid] || !/^OF-[A-Z0-9]{6}$/.test(String(oid))) continue;
    const o = await getOffer(env, oid);
    if (!o || o.cardId !== cid || o.status !== 'accepted' || Date.now() > o.checkoutBy)
      return json({ error: 'offer_invalid', cardId: cid, status: o ? (o.status === 'accepted' ? 'expired' : o.status) : 'missing' }, 409, origin);
    offerUse[cid] = o; items[cid] = 1;
  }

  for (let attempt = 0; attempt < 2; attempt++) {
    const st = await loadState(env);
    const cards = st.site.cards || {}, meta = st.site.meta || {};
    const askOf = (c) => (c.ask != null && c.ask !== '' ? Number(c.ask) : c.price != null ? Number(c.price) : null);
    const unavailable = [];
    const lines = [];
    for (const [id, q] of Object.entries(items)) {
      const c = cards[id];
      const left = c && !c.sold && askOf(c) != null ? (c.qty || 1) - (st.held[id] || 0) : 0;
      if (left < q) unavailable.push({ id, left: Math.max(0, left), name: c ? c.name : 'Unknown card' });
      else lines.push({ id, q, c, price: offerUse[id] ? offerUse[id].amount : askOf(c), offer: offerUse[id] ? offerUse[id].id : null });
    }
    if (unavailable.length) return json({ error: 'unavailable', items: unavailable }, 409, origin);

    const pct = meta.bundlePct ?? 2.5, max = meta.bundleMax ?? 10, shipFee = meta.shipping ?? 5, freeAt = meta.freeShipAt ?? 150;
    const sub = lines.reduce((n, x) => n + x.price * x.q, 0);
    const units = lines.reduce((n, x) => n + x.q, 0);
    const rate = units >= 2 ? Math.min(pct * units, max) : 0;
    const listSub = lines.filter((x) => !x.offer).reduce((n, x) => n + x.price * x.q, 0); // offer prices are final
    const disc = Math.round(listSub * rate) / 100;
    const after = sub - disc;
    const maxed = units >= 2 && rate >= max;
    const ship = after >= freeAt || maxed ? 0 : shipFee;
    const total = Math.round((after + ship) * 100) / 100;

    const rnd = crypto.getRandomValues(new Uint32Array(1))[0].toString(36).toUpperCase().padStart(6, '0').slice(-6);
    const id = 'LV-' + rnd;
    const keep = (st.holdsDoc.holds || []).filter((h) => !st.applied.has(h.order) || now - Date.parse(h.at) < 30 * 864e5);
    const holdsDoc = { holds: [...keep, { order: id, ids: items, total, app: f.app, at: new Date(now).toISOString() }] };
    const w = await writeJson(env, 'holds.json', holdsDoc, st.sha, `Hold cards for order ${id}`);
    if (w.status === 409 || w.status === 422) continue; // someone else wrote first: re-check stock and retry
    if (!w.ok) throw new Error(`GitHub write: ${w.status}`);
    holdsCache = { at: 0, held: null };
    recent.push(now); hits.set(ip, recent);
    for (const o of Object.values(offerUse)) await putOffer(env, { ...o, status: 'used', order: id });

    const list = lines.map((x) => `• ${x.c.name} (${x.c.set} #${x.c.number}) · ${x.c.cond}${x.q > 1 ? ' ×' + x.q : ''} — ${money(x.price * x.q)}${x.offer ? ' (accepted offer)' : ''}`).join('\n');
    const addr = [f.name, f.addr1, f.addr2, `${f.city}, ${f.state} ${f.zip}`].filter(Boolean).map(noLinks).join('\n');
    const payload = {
      username: 'Vault Orders', allowed_mentions: { parse: [] },
      content: `🛒 **New order ${id}** — ${money(total)} via ${f.app}. Cards are on hold; check for payment before shipping.`,
      embeds: [{
        title: `Order ${id}`, color: 0x5b3df5, timestamp: new Date(now).toISOString(),
        fields: [
          { name: `Cards (${units})`, value: list.slice(0, 1000) },
          { name: 'Subtotal', value: money(sub), inline: true },
          { name: 'Discount', value: disc ? `−${money(disc)} (${rate}%)` : 'none', inline: true },
          { name: 'Shipping', value: ship ? money(ship) : 'FREE', inline: true },
          { name: 'TOTAL', value: `**${money(total)}**`, inline: true },
          { name: 'Paying with', value: noLinks(`${f.app} · from "${f.paidName}"`).slice(0, 200), inline: true },
          { name: 'Ship to', value: addr.slice(0, 500) },
          { name: 'Contact', value: noLinks(f.contact).slice(0, 200) },
        ],
        footer: { text: 'Not paid? Relist the cards on the Owner desk to release the hold.' },
      }],
    };
    let discord = 0;
    try { discord = (await fetch(env.DISCORD_WEBHOOK, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).status; } catch { discord = -1; }
    return json({ ok: true, id, total, sub, disc, rate, ship, units, app: f.app, discord }, 200, origin);
  }
  return json({ error: 'busy' }, 503, origin);
}

// ---------- offers ----------
async function getOffer(env, id) { const v = await env.OFFERS.get('offer:' + id); return v ? JSON.parse(v) : null; }
async function putOffer(env, o) { await env.OFFERS.put('offer:' + o.id, JSON.stringify(o), { expirationTtl: 14 * 86400 }); }
async function signKey(env) {
  let k = await env.OFFERS.get('_sign_key');
  if (!k) { k = [...crypto.getRandomValues(new Uint8Array(32))].map((b) => b.toString(16).padStart(2, '0')).join(''); await env.OFFERS.put('_sign_key', k); }
  return crypto.subtle.importKey('raw', new TextEncoder().encode(k), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
}
async function sign(env, text) {
  const mac = await crypto.subtle.sign('HMAC', await signKey(env), new TextEncoder().encode(text));
  return [...new Uint8Array(mac)].slice(0, 16).map((b) => b.toString(16).padStart(2, '0')).join('');
}
function publicOffer(o) {
  const now = Date.now();
  let status = o.status;
  if (status === 'pending' && now > o.expires) status = 'expired';
  if (status === 'accepted' && now > o.checkoutBy) status = 'expired';
  return { id: o.id, cardId: o.cardId, amount: o.amount, ask: o.ask, status, checkoutBy: o.checkoutBy || null };
}

async function handleOffer(req, env, origin) {
  const ip = req.headers.get('CF-Connecting-IP') || 'x';
  const now = Date.now();
  const recent = (offerHits.get(ip) || []).filter((t) => now - t < 3600e3);
  if (recent.length >= MAX_OFFERS_PER_IP_HOUR) return json({ error: 'rate_limited' }, 429, origin);
  const raw = await req.text();
  if (raw.length > 2000) return json({ error: 'too_large' }, 413, origin);
  let b; try { b = JSON.parse(raw); } catch { return json({ error: 'bad_json' }, 400, origin); }
  const cardId = String(b.cardId || '');
  const amount = Math.round(Number(b.amount) * 100) / 100;
  const contact = clean(b.contact, 100);
  if (!/^\d{1,6}$/.test(cardId) || !(amount > 0)) return json({ error: 'bad_offer' }, 400, origin);
  if (!contact) return json({ error: 'missing', field: 'contact' }, 400, origin);

  const st = await loadState(env);
  const c = (st.site.cards || {})[cardId];
  const ask = c && (c.ask != null && c.ask !== '' ? Number(c.ask) : c.price != null ? Number(c.price) : null);
  const left = c && !c.sold && ask != null ? (c.qty || 1) - (st.held[cardId] || 0) : 0;
  if (left < 1) return json({ error: 'unavailable' }, 409, origin);
  const floor = Math.ceil(ask * (100 - OFFER_MAX_PCT)) / 100;
  if (amount < floor) return json({ error: 'too_low', floor }, 400, origin);
  if (amount >= ask) return json({ error: 'not_below_list', ask }, 400, origin);

  const id = 'OF-' + crypto.getRandomValues(new Uint32Array(1))[0].toString(36).toUpperCase().padStart(6, '0').slice(-6);
  const o = { id, cardId, cardName: c.name, ask, amount, status: 'pending', created: now, expires: now + OFFER_PENDING_MS };
  await putOffer(env, o);
  recent.push(now); offerHits.set(ip, recent);

  const base = new URL(req.url).origin;
  const link = async (act) => `${base}/offer/${id}/${act}?sig=${await sign(env, id + ':' + act)}`;
  const pctOff = Math.round((1 - amount / ask) * 1000) / 10;
  const payload = {
    username: 'Vault Orders', allowed_mentions: { parse: [] }, flags: 4, // 4 = no link previews (so nothing auto-opens the links)
    content: `💬 **Offer ${id}** on **${c.name}** (${c.set} #${c.number} · ${c.cond}): **${money(amount)}** — list ${money(ask)}, ${pctOff}% under.\n` +
      `From: ${noLinks(contact)}\n✅ [Accept](${await link('accept')})   ·   ❌ [Decline](${await link('decline')})\n` +
      `Undecided offers expire in 3 days. Accepting doesn't hold the card; the buyer still has to check out.`,
  };
  try { await fetch(env.DISCORD_WEBHOOK, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); } catch {}
  return json({ ok: true, offer: publicOffer(o) }, 200, origin);
}

const page = (title, body, color = '#5b3df5') => new Response(
  `<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>${title}</title>` +
  `<body style="font-family:system-ui,sans-serif;background:#f4f3fb;color:#17142e;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px">` +
  `<div style="background:#fff;border:1px solid #dcd9ec;border-radius:16px;padding:24px;max-width:420px;width:100%"><h2 style="margin:0 0 8px;color:${color}">${title}</h2>${body}</div>`,
  { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex' } });
const escH = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function handleDecision(req, env, id, act) {
  const url = new URL(req.url);
  const sig = url.searchParams.get('sig') || '';
  if (!/^OF-[A-Z0-9]{6}$/.test(id) || sig !== (await sign(env, id + ':' + act))) return page('Link not valid', '<p>This link is broken or was changed.</p>', '#c7383f');
  const o = await getOffer(env, id);
  if (!o) return page('Offer not found', '<p>It may have expired.</p>', '#c7383f');
  const what = `${escH(o.cardName)} at <b>$${o.amount.toFixed(2)}</b> (list $${o.ask.toFixed(2)})`;
  if (o.status !== 'pending') return page(`Already ${o.status}`, `<p>Offer ${id}: ${what}.</p>`);
  if (Date.now() > o.expires) return page('Offer expired', `<p>Offer ${id}: ${what} was never answered in time.</p>`, '#c7383f');
  if (req.method !== 'POST') {
    const label = act === 'accept' ? 'Accept offer' : 'Decline offer';
    const bg = act === 'accept' ? '#0f8a5f' : '#c7383f';
    return page(`${act === 'accept' ? 'Accept' : 'Decline'} offer ${id}?`, `<p>${what}</p>` +
      `<form method=post><button style="width:100%;padding:12px;border:0;border-radius:10px;background:${bg};color:#fff;font-weight:800;font-size:15px">${label}</button></form>`);
  }
  if (act === 'accept') {
    const st = await loadState(env);
    const c = (st.site.cards || {})[o.cardId];
    const left = c && !c.sold ? (c.qty || 1) - (st.held[o.cardId] || 0) : 0;
    if (left < 1) { await putOffer(env, { ...o, status: 'declined', reason: 'sold' }); return page('Card already sold', `<p>${what} sold before you answered, so the offer was declined automatically.</p>`, '#c7383f'); }
    await putOffer(env, { ...o, status: 'accepted', decidedAt: Date.now(), checkoutBy: Date.now() + OFFER_CHECKOUT_MS });
    return page('Offer accepted ✅', `<p>${what}.</p><p>The buyer's page now shows a button to check out at that price for the next 48 hours. You'll get the usual order ping when they do.</p>`, '#0f8a5f');
  }
  await putOffer(env, { ...o, status: 'declined', decidedAt: Date.now() });
  return page('Offer declined', `<p>${what}. The buyer's page now shows it was declined.</p>`);
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const origin = req.headers.get('Origin') || '';
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors(origin) });
    try {
      if (url.pathname === '/holds' && req.method === 'GET') {
        if (!holdsCache.held || Date.now() - holdsCache.at > 10e3) {
          const st = await loadState(env);
          holdsCache = { at: Date.now(), held: st.held };
        }
        return json({ held: holdsCache.held }, 200, origin);
      }
      if (url.pathname === '/order' && req.method === 'POST') {
        if (origin && !ORIGINS.includes(origin)) return json({ error: 'origin' }, 403, origin);
        return await handleOrder(req, env, origin);
      }
      if (url.pathname === '/offer' && req.method === 'POST') {
        if (origin && !ORIGINS.includes(origin)) return json({ error: 'origin' }, 403, origin);
        return await handleOffer(req, env, origin);
      }
      let m = url.pathname.match(/^\/offer\/(OF-[A-Z0-9]{6})$/);
      if (m && req.method === 'GET') { const o = await getOffer(env, m[1]); return o ? json({ offer: publicOffer(o) }, 200, origin) : json({ error: 'not_found' }, 404, origin); }
      m = url.pathname.match(/^\/offer\/(OF-[A-Z0-9]{6})\/(accept|decline)$/);
      if (m && (req.method === 'GET' || req.method === 'POST')) return await handleDecision(req, env, m[1], m[2]);
      if (url.pathname === '/') return json({ ok: true, service: 'vault-orders' }, 200, origin);
      return json({ error: 'not_found' }, 404, origin);
    } catch (e) {
      return json({ error: 'server', detail: String(e.message || e).slice(0, 200) }, 500, origin);
    }
  },
};

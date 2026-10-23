// Vault Orders relay — Cloudflare Worker for the Lazy Lucario Vault storefront.
// Secrets (set in Cloudflare, never in this file): GITHUB_TOKEN, DISCORD_WEBHOOK
// GET  /holds  -> {held:{cardId:qty}}  cards on hold from orders not yet applied to the Owner desk
// POST /order  -> checks stock + prices server-side, records the hold in holds.json, pings Discord
const OWNER = 'lazylucariospokemart', REPO = 'lazy-lucario-vault';
const ORIGINS = ['https://lazylucariospokemart.github.io'];
const MAX_ORDERS_PER_IP_HOUR = 5;
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
      else lines.push({ id, q, c, price: askOf(c) });
    }
    if (unavailable.length) return json({ error: 'unavailable', items: unavailable }, 409, origin);

    const pct = meta.bundlePct ?? 2.5, max = meta.bundleMax ?? 10, shipFee = meta.shipping ?? 5, freeAt = meta.freeShipAt ?? 150;
    const sub = lines.reduce((n, x) => n + x.price * x.q, 0);
    const units = lines.reduce((n, x) => n + x.q, 0);
    const rate = units >= 2 ? Math.min(pct * units, max) : 0;
    const disc = Math.round(sub * rate) / 100;
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

    const list = lines.map((x) => `• ${x.c.name} (${x.c.set} #${x.c.number}) · ${x.c.cond}${x.q > 1 ? ' ×' + x.q : ''} — ${money(x.price * x.q)}`).join('\n');
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
      if (url.pathname === '/') return json({ ok: true, service: 'vault-orders' }, 200, origin);
      return json({ error: 'not_found' }, 404, origin);
    } catch (e) {
      return json({ error: 'server', detail: String(e.message || e).slice(0, 200) }, 500, origin);
    }
  },
};

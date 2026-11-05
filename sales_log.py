"""Realized-sales log for the vault (run by build_site.py on every Vault Publisher sync).

Keeps sales.json up to date: one entry per card that has been marked sold on the Owner desk.
This is the ground-truth feed the Foil Ledger API sells as /v1/sold — what this one shop
actually got for a card, next to the market price at the time.

How an entry is built (first value that exists wins):
  soldPrice  = card.soldPrice (if Lazy sets one on the desk) → card.ask → card.price (market)
  priceBasis = "desk" | "ask" | "market"  (says which of those was used; be honest with buyers)
  soldAt     = card.soldAt → the vault order's timestamp (holds.json) → first sync that saw it sold
  channel    = card.soldVia → "vault" when a vault order id is attached → "offline" (card shows etc.)
  market     = the TCGplayer-based market price + 7-day change at the time the sale was logged

Cards that go back to unsold (a hold that fell through, a relist) are dropped from the log.
Backfilled entries (cards already sold before the log existed) carry backfilled: true and
soldAt: null unless an order timestamp is known. Nothing private is written: no notes,
no buyer info, no order totals (an order total includes shipping and other cards).
Usage: imported by build_site.py; or `python3 -I sales_log.py` to refresh sales.json by hand.
"""
import json, os, glob, datetime

LOG = 'sales.json'
HOLDS = 'holds.json'

PUBLIC = ('name', 'set', 'number', 'cond', 'printing', 'rarity', 'language', 'grade', 'grader', 'sealedType')


def _num(v):
    try:
        return None if v is None or v == '' else round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _orders():
    """(order id -> ISO timestamp, card id -> order id) from holds.json, applied orders only."""
    if not os.path.exists(HOLDS):
        return {}, {}
    try:
        doc = json.load(open(HOLDS))
        applied = set(json.load(open('export/meta/orders.json')).get('applied', [])) if os.path.exists('export/meta/orders.json') else set()
    except Exception:
        return {}, {}
    times, by_card = {}, {}
    for h in doc.get('holds', []):
        o = h.get('order')
        if not o or o not in applied:
            continue
        times[o] = h.get('at')
        for cid in (h.get('ids') or {}):
            by_card[str(cid)] = o
    return times, by_card


def _entry(card_id, c, now_iso, order_at, order_of, first_run):
    order = c.get('soldOrder') or order_of.get(str(card_id)) or None
    desk_price = _num(c.get('soldPrice'))
    if desk_price is not None:
        price, basis = desk_price, 'desk'
    elif _num(c.get('ask')) is not None:
        price, basis = _num(c.get('ask')), 'ask'
    else:
        price, basis = _num(c.get('price')), 'market'
    sold_at = c.get('soldAt') or order_at.get(order) or (None if first_run else now_iso)
    channel = c.get('soldVia') or ('vault' if order else 'offline')
    e = {'id': str(card_id), 'kind': c.get('kind') or 'single'}
    for k in PUBLIC:
        if c.get(k) not in (None, ''):
            e[k] = c[k]
    e.update({
        'tcgId': c.get('tcgId') or c.get('img') or None,
        'soldPrice': price,
        'priceBasis': basis,
        'soldAt': sold_at,
        'channel': channel,
        'market': {'price': _num(c.get('price')), 'chg7': c.get('chg7'), 'at': c.get('priceAt')},
        'loggedAt': now_iso,
    })
    if first_run and not sold_at:
        e['backfilled'] = True
    return e


def update(cards, now=None):
    """cards: {id: card dict} from export/cards. Returns (entries, added, removed)."""
    now_iso = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec='seconds').replace('+00:00', 'Z')
    existing = []
    if os.path.exists(LOG):
        try:
            existing = json.load(open(LOG)).get('sales', [])
        except Exception:
            existing = []
    first_run = not existing
    by_id = {e['id']: e for e in existing}
    order_at, order_of = _orders()

    kept, added, removed = [], 0, 0
    sold_ids = {str(i) for i, c in cards.items() if c.get('sold')}
    for e in existing:
        if e['id'] in sold_ids:
            kept.append(e)
        else:
            removed += 1
    for card_id in sorted(sold_ids, key=lambda s: (len(s), s)):
        if card_id in by_id:
            continue
        kept.append(_entry(card_id, cards[card_id], now_iso, order_at, order_of, first_run))
        added += 1

    kept.sort(key=lambda e: (e.get('soldAt') or '', e['id']))
    doc = {
        'what': "Realized sales from Lazy Lucario Vault: what one working Pokémon card shop actually got for each card, with the market price at the time. priceBasis says where soldPrice came from (desk = entered by the seller, ask = the listed asking price, market = no asking price on file, market value used).",
        'count': len(kept),
        'updatedAt': now_iso,
        'sales': kept,
    }
    json.dump(doc, open(LOG, 'w'), separators=(',', ':'))
    return kept, added, removed


if __name__ == '__main__':
    cards = {os.path.splitext(os.path.basename(f))[0]: json.load(open(f)) for f in glob.glob('export/cards/*.json')}
    entries, added, removed = update(cards)
    print(f'sales.json: {len(entries)} sales ({added} added, {removed} removed)')

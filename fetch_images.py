"""Save card pictures into img/ (runs on GitHub Actions, see .github/workflows/images.yml).

Two jobs:
  1. Scanner pictures: any image dropped in incoming/ whose filename starts with a
     TCGplayer id (42367.jpg, 42367_front.png, ...) is resized to a max height of
     437px and saved as img/<id>.jpg. The original is then moved to incoming/done/.
  2. Missing pictures: every card in export/cards/ with an "img" id but no img/<id>.jpg
     is downloaded from TCGplayer's image CDN, resized and saved the same way.

This script only ever ADDS files to img/. It never deletes or overwrites anything.
Usage: python3 -I fetch_images.py
"""
import glob, io, json, os, re, shutil, sys, urllib.request

MAX_H = 437
IMG_DIR = 'img'
INCOMING = 'incoming'
DONE = os.path.join(INCOMING, 'done')
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
URLS = [
    'https://tcgplayer-cdn.tcgplayer.com/product/{id}_in_1000x1000.jpg',
    'https://product-images.tcgplayer.com/fit-in/437x437/{id}.jpg',
]
UA = {'User-Agent': 'Mozilla/5.0 (compatible; LazyLucarioVault/1.0)'}

try:
    from PIL import Image
except ImportError:
    sys.exit('Pillow is required: pip install pillow')


def save_resized(data, dest):
    """Resize image bytes to max height MAX_H and write a JPEG at dest. Never overwrites."""
    if os.path.exists(dest):
        return False
    im = Image.open(io.BytesIO(data))
    im.load()
    if im.mode in ('RGBA', 'LA', 'P'):
        bg = Image.new('RGB', im.size, (255, 255, 255))
        bg.paste(im.convert('RGBA'), mask=im.convert('RGBA').split()[3])
        im = bg
    elif im.mode != 'RGB':
        im = im.convert('RGB')
    w, h = im.size
    if h > MAX_H:
        im = im.resize((max(1, round(w * MAX_H / h)), MAX_H), Image.LANCZOS)
    im.save(dest, 'JPEG', quality=88, optimize=True)
    return True


def scanner_pictures():
    os.makedirs(DONE, exist_ok=True)
    saved = moved = 0
    for path in sorted(glob.glob(os.path.join(INCOMING, '*'))):
        if not os.path.isfile(path):
            continue
        name = os.path.basename(path)
        if not name.lower().endswith(IMAGE_EXT):
            continue
        m = re.match(r'^(\d+)', name)
        if not m:
            print(f'skip {name}: filename does not start with a TCGplayer id')
            continue
        tid = m.group(1)
        dest = os.path.join(IMG_DIR, f'{tid}.jpg')
        try:
            with open(path, 'rb') as f:
                data = f.read()
            if save_resized(data, dest):
                saved += 1
                print(f'saved {dest} from {name}')
            else:
                print(f'kept existing {dest}; {name} not used')
        except Exception as e:  # a bad image never stops the run
            print(f'could not read {name}: {e}')
            continue
        target = os.path.join(DONE, name)
        if os.path.exists(target):
            base, ext = os.path.splitext(name)
            n = 2
            while os.path.exists(os.path.join(DONE, f'{base}_{n}{ext}')):
                n += 1
            target = os.path.join(DONE, f'{base}_{n}{ext}')
        shutil.move(path, target)
        moved += 1
    return saved, moved


def download(tid):
    for url in URLS:
        try:
            req = urllib.request.Request(url.format(id=tid), headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            if data and len(data) > 500:
                return data, url
        except Exception as e:
            print(f'  {url.format(id=tid)}: {e}')
    return None, None


def missing_pictures():
    ids = set()
    for f in glob.glob('export/cards/*.json'):
        try:
            d = json.load(open(f))
            d = d.get('data', d)
        except Exception:
            continue
        tid = str(d.get('img') or '').strip()
        if tid.isdigit():
            ids.add(tid)
    want = sorted(t for t in ids if not os.path.exists(os.path.join(IMG_DIR, f'{t}.jpg')))
    print(f'{len(ids)} cards with img ids, {len(want)} missing pictures')
    saved = failed = 0
    for tid in want:
        data, src = download(tid)
        if not data:
            failed += 1
            print(f'no picture found for {tid}')
            continue
        try:
            if save_resized(data, os.path.join(IMG_DIR, f'{tid}.jpg')):
                saved += 1
                print(f'saved img/{tid}.jpg from {src.split("/")[2]}')
        except Exception as e:
            failed += 1
            print(f'bad image for {tid}: {e}')
    return saved, failed


if __name__ == '__main__':
    os.makedirs(IMG_DIR, exist_ok=True)
    s1, moved = scanner_pictures()
    s2, failed = missing_pictures()
    print(f'done: {s1} scanner pictures saved ({moved} moved to incoming/done), '
          f'{s2} pictures downloaded, {failed} not found')

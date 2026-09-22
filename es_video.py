# -*- coding: utf-8 -*-
"""englishscool.co video lessons - one vertical short (1080x1920, <= 3:00) per item.

Built on the learnfrench v3 pipeline (timed stills -> ffmpeg concat, music bed at the
approved flat 0.62, one mastering pass to -14 LUFS / -1.5 dBTP) with the rules that
bind every clip: narration TEACHES (names the subject, states the mechanic, gives
when-to-use and a takeaway), the CTA is the item's real page, and every English
specimen is read by a second voice at a slower rate. English-only site: no routing.

  python es_video.py <slug> [<slug> ...]      render into videos/<slug>.mp4 (+ .vtt, .jpg)
  python es_video.py --all [--type lesson]    every item from items.json, skipping done ones
"""
import os, sys, io, re, json, math, html, hashlib, asyncio, subprocess, time
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'videos'); TMP = os.path.join(HERE, 'tmp')
os.makedirs(OUT, exist_ok=True); os.makedirs(TMP, exist_ok=True)
FF = 'ffmpeg'
W, H, FPS = 1080, 1920, 30
CAP = 176.0                          # Reels / Shorts / Bluesky cap is 3:00
SITE = 'englishscool.co'

# ---- palette: the site's ACSS tokens, light scheme, converted to sRGB ----
PAPER = (239, 238, 239); CARD = (250, 249, 250); INK = (20, 16, 20); MUTED = (101, 98, 101)
PRIMARY = (88, 51, 85); PRIMARY_DK = (68, 34, 65); PINK = (249, 233, 247); LINE = (210, 208, 210)
ACCENT = (217, 149, 29); SUCCESS = (49, 122, 69); DANGER = (182, 59, 53)
BEDS = [os.path.join(HERE, b) for b in ('bed-M1-marseille-ney.wav', 'bed-M2-marseille-oud.wav', 'bed-M3-marseille-sunny.wav')]
BED_VOL = 0.62
NARR = ('en-GB-RyanNeural', '-4%')      # the teacher
SPEC = ('en-GB-SoniaNeural', '-10%')    # every English specimen, a little slower
SLOW = ('en-GB-SoniaNeural', '-25%')
DLG = [('en-GB-ThomasNeural', '-6%'), ('en-GB-SoniaNeural', '-6%'), ('en-GB-LibbyNeural', '-6%')]

FD = os.path.join(HERE, 'fonts')
def _font(name, size):
    p = os.path.join(FD, name)
    try: return ImageFont.truetype(p, size)
    except Exception: return ImageFont.truetype(os.path.join(r'C:\Windows\Fonts', 'georgia.ttf' if 'Serif' in name else 'segoeui.ttf'), size)
SERIF = lambda s: _font('IBMPlexSerif-SemiBold.ttf', s)
SANS = lambda s: _font('IBMPlexSans-Regular.ttf', s)
SANSB = lambda s: _font('IBMPlexSans-SemiBold.ttf', s)
MONO = lambda s: _font('IBMPlexMono-Regular.ttf', s)

SAFE_L, SAFE_R, SAFE_T, SAFE_B = 84, 996, 250, 1560

# the site fonts carry no IPA glyphs; IPA text falls back to Segoe UI (full coverage)
IPA_RE = re.compile('[ɐ-ʯːˑ]')

# ---------------- text helpers ----------------
def wrap(d, text, font, maxw):
    lines = []
    for para in text.split('\n'):
        words = para.split(' '); cur = ''
        for w in words:
            t = (cur + ' ' + w).strip()
            if d.textlength(t, font=font) <= maxw or not cur: cur = t
            else: lines.append(cur); cur = w
        lines.append(cur)
    return lines

def fit(d, text, mk, maxw, start, minsz, maxlines):
    if IPA_RE.search(text or ''):
        mk = lambda s: _font('segoeui.ttf', s)
    for s in range(start, minsz - 1, -2):
        f = mk(s); ls = wrap(d, text, f, maxw)
        if len(ls) <= maxlines: return f, ls
    f = mk(minsz); ls = wrap(d, text, f, maxw)
    if len(ls) > maxlines:
        ls = ls[:maxlines]; ls[-1] = ls[-1].rstrip('.,;:') + '\u2026'
    return f, ls

def sentences(text, n=None):
    text = re.sub(r'\s+', ' ', (text or '').replace('\n', ' ')).strip()
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"\'(])', text)
    parts = [p.strip() for p in parts if p.strip()]
    return parts[:n] if n else parts

def chunks(sents, per=2):
    return [' '.join(sents[i:i + per]) for i in range(0, len(sents), per)]

def strip_tags(s):
    return html.unescape(re.sub(r'<[^>]+>', ' ', s or '')).replace('\xa0', ' ')

def clean(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()

def speakable(s):
    """text -> what the voice says (gaps become 'blank', bracket hints become asides)."""
    s = s.replace('___', ' blank ')
    s = re.sub(r'\(([^)]+)\)', r', \1,', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

# ---------------- painters ----------------
def canvas():
    im = Image.new('RGB', (W, H), PAPER); d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 14], fill=PRIMARY)
    return im, d

def chrome(d, kicker, prog):
    d.text((SAFE_L, 92), SITE, font=SERIF(46), fill=PRIMARY)
    f = SANSB(28); k = kicker.upper()
    while d.textlength(k, font=f) > (SAFE_R - SAFE_L) and len(k) > 8: k = k[:-2].rstrip() + '\u2026'
    d.text((SAFE_L, 156), k, font=f, fill=MUTED)
    d.rectangle([SAFE_L, 205, SAFE_R, 209], fill=LINE)
    d.rectangle([SAFE_L, 205, SAFE_L + int((SAFE_R - SAFE_L) * prog), 209], fill=ACCENT)

def label(d, text, y):
    d.text((SAFE_L, y), text.upper(), font=SANSB(30), fill=PRIMARY)
    return y + 54

def cta_strip(d, path, line):
    y = 1620
    d.rectangle([0, y, W, H], fill=PRIMARY)
    f, ls = fit(d, line, SANSB, SAFE_R - SAFE_L, 48, 34, 2)
    yy = y + 46
    for l in ls: d.text((SAFE_L, yy), l, font=f, fill=CARD); yy += f.size + 8
    fm = MONO(44); url = SITE + '/' + path.strip('/') + '/'
    while d.textlength(url, font=fm) > (SAFE_R - SAFE_L) and fm.size > 34: fm = MONO(fm.size - 2)
    lines = [url]
    if d.textlength(url, font=fm) > (SAFE_R - SAFE_L):
        # long slug: break after the section, never shrink the URL below legibility
        cut = url.rfind('/', 0, len(url) - 1) + 1
        lines = [url[:cut], url[cut:]] if cut > len(SITE) + 1 else [url]
        while any(d.textlength(l, font=fm) > (SAFE_R - SAFE_L) for l in lines) and fm.size > 26: fm = MONO(fm.size - 2)
    yy += 26
    for l in lines: d.text((SAFE_L, yy), l, font=fm, fill=(240, 176, 79)); yy += fm.size + 8

def centered(kicker, prog, body, bottom=SAFE_B):
    """Paint `body(d, y) -> y_end` twice: once to measure, once vertically centred in the safe area."""
    scratch = Image.new('RGB', (W, H), PAPER); ds = ImageDraw.Draw(scratch)
    h = body(ds, 0)
    avail = bottom - SAFE_T
    y0 = SAFE_T + max(0, (avail - h) // 2) - 20
    im, d = canvas(); chrome(d, kicker, prog)
    body(d, y0)
    return im

def paint_title(kicker, prog, title, sub=None):
    def body(d, y):
        f, ls = fit(d, title, SERIF, SAFE_R - SAFE_L, 116, 64, 5)
        for l in ls: d.text((SAFE_L, y), l, font=f, fill=INK); y += int(f.size * 1.16)
        d.rectangle([SAFE_L, y + 30, SAFE_L + 180, y + 42], fill=ACCENT); y += 42
        if sub:
            fs, ls2 = fit(d, sub, SANS, SAFE_R - SAFE_L, 50, 36, 6)
            y += 80
            for l in ls2: d.text((SAFE_L, y), l, font=fs, fill=MUTED); y += int(fs.size * 1.35)
        return y
    return centered(kicker, prog, body)

def paint_english(kicker, prog, lab, text, note=None, tone=None, hi=None):
    """the protected-English box: big bold sentence on the pale pink card."""
    def body(d, y):
        y = label(d, lab, y)
        f, ls = fit(d, text, SANSB, SAFE_R - SAFE_L - 60, 84, 44, 9)
        hgt = len(ls) * int(f.size * 1.28) + 100
        fill = PINK if tone is None else ((236, 246, 238) if tone == 'ok' else (250, 236, 234))
        d.rounded_rectangle([SAFE_L - 24, y, SAFE_R + 24, y + hgt], radius=26, fill=fill, outline=(LINE if tone is None else (SUCCESS if tone == 'ok' else DANGER)), width=4)
        yy = y + 50
        for l in ls:
            x = SAFE_L + 6
            if hi:
                for w in l.split(' '):
                    ww = d.textlength(w + ' ', font=f)
                    if norm(w) in hi: d.rectangle([x - 4, yy - 4, x + ww - 10, yy + f.size + 8], fill=(255, 233, 180))
                    d.text((x, yy), w, font=f, fill=INK); x += ww
            else:
                d.text((x, yy), l, font=f, fill=INK)
            yy += int(f.size * 1.28)
        y = y + hgt
        if note:
            fn, ln = fit(d, note, SANS, SAFE_R - SAFE_L, 44, 34, 6)
            y += 44
            for l in ln: d.text((SAFE_L, y), l, font=fn, fill=MUTED); y += int(fn.size * 1.4)
        return y
    return centered(kicker, prog, body)

def paint_prose(kicker, prog, lab, text, big=False):
    def body(d, y):
        y = label(d, lab, y)
        f, ls = fit(d, text, SANS, SAFE_R - SAFE_L, 62 if big else 56, 38, 16)
        for l in ls: d.text((SAFE_L, y), l, font=f, fill=INK); y += int(f.size * 1.42)
        return y
    return centered(kicker, prog, body)

def paint_list(kicker, prog, lab, items, numbered=False, english=True):
    def body(d, y):
        y = label(d, lab, y)
        n = len(items); size = 60 if n <= 3 else (54 if n <= 5 else 46)
        for i, it in enumerate(items):
            f, ls = fit(d, it, SANSB if english else SANS, SAFE_R - SAFE_L - 74, size, 34, 4)
            if numbered: d.text((SAFE_L, y), '%d.' % (i + 1), font=SANSB(size), fill=PRIMARY)
            else: d.ellipse([SAFE_L + 4, y + int(size * 0.42), SAFE_L + 4 + int(size * 0.4), y + int(size * 0.82)], fill=ACCENT)
            for l in ls: d.text((SAFE_L + 74, y), l, font=f, fill=INK); y += int(f.size * 1.3)
            y += 34
        return y
    return centered(kicker, prog, body)

def paint_rows(kicker, prog, lab, rows):
    def body(d, y):
        y = label(d, lab, y)
        for a, b, extra in rows:
            fa, la = fit(d, a, SANSB, SAFE_R - SAFE_L, 68, 44, 2)
            for l in la: d.text((SAFE_L, y), l, font=fa, fill=INK); y += int(fa.size * 1.22)
            y += 10
            sub = ' · '.join([b] + extra)
            fb, lb = fit(d, sub, SANS, SAFE_R - SAFE_L, 46, 34, 4)
            for l in lb: d.text((SAFE_L, y), l, font=fb, fill=MUTED); y += int(fb.size * 1.35)
            y += 50
            d.rectangle([SAFE_L, y - 24, SAFE_R, y - 21], fill=LINE)
        return y
    return centered(kicker, prog, body)

def paint_line(kicker, prog, who, text, idx, n):
    def body(d, y):
        d.text((SAFE_L, y), '%d / %d' % (idx, n), font=SANSB(34), fill=MUTED); y += 90
        col = PRIMARY if who in ('A', 'P', 'E') else ACCENT
        d.ellipse([SAFE_L, y, SAFE_L + 96, y + 96], fill=col)
        d.text((SAFE_L + 30, y + 16), who, font=SANSB(54), fill=CARD)
        f, ls = fit(d, text, SANSB, SAFE_R - SAFE_L - 130, 66, 40, 10)
        yy = y + 6
        for l in ls: d.text((SAFE_L + 130, yy), l, font=f, fill=INK); yy += int(f.size * 1.3)
        return max(yy, y + 100)
    return centered(kicker, prog, body)

def paint_cta(kicker, take, path, line):
    def body(d, y):
        y = label(d, 'Say it today', y)
        f, ls = fit(d, take, SANSB, SAFE_R - SAFE_L - 60, 76, 42, 7)
        hgt = len(ls) * int(f.size * 1.28) + 100
        d.rounded_rectangle([SAFE_L - 24, y, SAFE_R + 24, y + hgt], radius=26, fill=PINK, outline=LINE, width=4)
        yy = y + 50
        for l in ls: d.text((SAFE_L + 6, yy), l, font=f, fill=INK); yy += int(f.size * 1.28)
        return y + hgt
    im = centered(kicker, 1.0, body, bottom=1560)
    cta_strip(ImageDraw.Draw(im), path, line)
    return im

def norm(w): return re.sub(r"[^\w'-]", '', w.lower())

# ---------------- audio ----------------
async def _tts(text, voice, rate, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(path)

def synth(lines, tag):
    """lines: [(text, (voice, rate)), ...] -> [(mp3, dur, text)] with network backoff."""
    out = []
    for i, (text, vr) in enumerate(lines):
        p = os.path.join(TMP, '%s-%02d.mp3' % (tag, i))
        key = hashlib.sha1(('%s|%s|%s' % (text, vr[0], vr[1])).encode('utf-8')).hexdigest()[:12]
        p = os.path.join(TMP, 'tts-%s.mp3' % key)
        if not os.path.exists(p) or os.path.getsize(p) < 500:
            for back in (30, 90, 300, 600, 0):
                try:
                    asyncio.run(_tts(text, vr[0], vr[1], p)); break
                except Exception as e:
                    s = str(e).lower()
                    if not back or not any(k in s for k in ('connect', 'getaddrinfo', 'ssl', 'timeout', 'reset', 'refused', 'temporar', '403', '429')):
                        raise
                    print('   network: %s - waiting %ds' % (str(e)[:80], back), flush=True); time.sleep(back)
        out.append((p, duration(p), text))
    return out

def duration(p):
    r = subprocess.run([FF, '-i', p, '-f', 'null', '-'], capture_output=True, text=True, errors='replace')
    m = re.findall(r'time=(\d+):(\d+):([\d.]+)', r.stderr)
    if not m: return 1.0
    h, mi, s = m[-1]; return int(h) * 3600 + int(mi) * 60 + float(s)

def measure_lufs(wav):
    p = subprocess.run([FF, '-hide_banner', '-nostats', '-i', wav, '-af', 'loudnorm=print_format=json', '-f', 'null', '-'], capture_output=True, text=True, errors='replace')
    m = re.search(r'\{[^{}]*input_i[^{}]*\}', p.stdout + p.stderr, re.S)
    return json.loads(m.group(0)) if m else None

def encode(stills, durs, segs, total, out_mp4, slug, cues, out_vtt):
    lst = os.path.join(TMP, slug + '-concat.txt')
    with io.open(lst, 'w', encoding='utf-8') as f:
        for p, dur in zip(stills, durs): f.write("file '%s'\nduration %.3f\n" % (p.replace('\\', '/'), dur))
        f.write("file '%s'\n" % stills[-1].replace('\\', '/'))
    bed = BEDS[hashlib.sha1(slug.encode()).digest()[0] % len(BEDS)]
    mix = os.path.join(TMP, slug + '-mix.wav')
    args = [FF, '-y', '-stream_loop', '-1', '-i', bed]
    for p, _ in segs: args += ['-i', p]
    parts = ['[0:a]atrim=0:%.2f,volume=%.2f[bed]' % (total, BED_VOL)]
    for i, (_, off) in enumerate(segs): parts.append('[%d:a]adelay=%d:all=1[v%d]' % (i + 1, int(off * 1000), i))
    parts.append('[bed]' + ''.join('[v%d]' % i for i in range(len(segs))) + 'amix=inputs=%d:normalize=0:duration=first[aout]' % (len(segs) + 1))
    args += ['-filter_complex', ';'.join(parts), '-map', '[aout]', '-ar', '48000', mix]
    r = subprocess.run(args, capture_output=True, text=True, errors='replace')
    if not os.path.exists(mix): raise RuntimeError('premix failed: ' + r.stderr[-400:])
    meas = measure_lufs(mix)
    gain = max(-20.0, min(20.0, -14.0 - float(meas['input_i']))) if meas else 0.0
    args = [FF, '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-i', mix,
            '-filter_complex', '[1:a]volume=%.2fdB,alimiter=limit=0.8414:level=false[a]' % gain,
            '-map', '0:v', '-map', '[a]', '-r', str(FPS), '-pix_fmt', 'yuv420p',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '23', '-c:a', 'aac', '-b:a', '128k',
            '-shortest', '-movflags', '+faststart', out_mp4]
    r = subprocess.run(args, capture_output=True, text=True, errors='replace')
    if not os.path.exists(out_mp4): raise RuntimeError('encode failed: ' + r.stderr[-400:])
    with io.open(out_vtt, 'w', encoding='utf-8') as f:
        f.write('WEBVTT\n\n')
        ts = lambda x: '%02d:%02d:%06.3f' % (int(x // 3600), int(x % 3600 // 60), x % 60)
        for i, (a, b, t) in enumerate(cues, 1): f.write('%d\n%s --> %s\n%s\n\n' % (i, ts(a), ts(b), t))
    # poster = first still, as jpg (never an empty poster attribute later)
    Image.open(stills[0]).convert('RGB').save(out_mp4[:-4] + '.jpg', quality=82)
    os.remove(mix)

# ---------------- beat scheduling ----------------
def schedule(beats, clips, cap):
    """beats: [(cards, nspoken, pad_s, min_s)]; clips in order. Speech is never cut, only slack."""
    li = 0; nat, speech = [], []
    for cards, n, pad, mn in beats:
        sp = sum(c[1] for c in clips[li:li + n]) + 0.25 * max(0, n - 1); li += n
        speech.append(sp); nat.append(max(mn, sp + pad))
    total = sum(nat); scale = 1.0
    if total > cap:
        slack = total - sum(speech); need = total - cap
        if slack <= 0.5 or need > slack * 0.92:
            raise RuntimeError('over cap: speech alone %.1fs' % sum(speech))
        scale = 1.0 - need / slack
    li = 0; t = 0.0; stills, durs, segs, cues = [], [], [], []
    for k, (cards, n, pad, mn) in enumerate(beats):
        dur = speech[k] + (nat[k] - speech[k]) * scale
        if len(cards) == 2 and n == 2:
            # reveal beat: [gap card, answer card] with [question clip, answer clip].
            # The thinking pause sits on the gap card; the answer is spoken as the reveal appears.
            q, a = clips[li], clips[li + 1]
            slack = max(0.0, dur - (q[1] + a[1] + 0.25))
            per = [q[1] + slack * 0.75 + 0.15, a[1] + slack * 0.25 + 0.1]
            stills += cards; durs += per
            segs.append((q[0], t + 0.15)); cues.append((t + 0.15, t + 0.15 + q[1], q[2]))
            off = t + per[0] + 0.05
            segs.append((a[0], off)); cues.append((off, off + a[1], a[2]))
            li += 2; t += sum(per); continue
        per = [dur / len(cards)] * len(cards)
        for im, dd in zip(cards, per): stills.append(im); durs.append(dd)
        off = t + 0.15
        for c in clips[li:li + n]:
            segs.append((c[0], off)); cues.append((off, off + c[1], c[2])); off += c[1] + 0.25
        li += n; t += dur
    return stills, durs, segs, cues, t

def save_stills(images, slug):
    d = os.path.join(TMP, 'st'); os.makedirs(d, exist_ok=True); out = []
    for i, im in enumerate(images):
        p = os.path.join(d, '%s-%03d.png' % (slug, i)); im.save(p); out.append(p)
    return out

def render(slug, beats_fn, path, cap=CAP):
    """beats_fn(prog) -> list of (cards, [(text, voice)], pad, min) built lazily so the
    progress bar can be painted; returns the mp4 path."""
    out_mp4 = os.path.join(OUT, slug + '.mp4'); out_vtt = out_mp4[:-4] + '.vtt'
    spec = beats_fn()
    n = len(spec)
    cards_all, lines_all, beats = [], [], []
    for i, (painters, lines, pad, mn) in enumerate(spec):
        prog = (i + 1) / n
        cards = [p(prog) for p in painters]
        cards_all.append(cards); lines_all += lines
        beats.append((cards, len(lines), pad, mn))
    clips = synth(lines_all, slug)
    flat = []
    for cards, nn, pad, mn in beats: flat.append((cards, nn, pad, mn))
    images = [im for cards, _, _, _ in flat for im in cards]
    paths = save_stills(images, slug)
    it = iter(paths); beats_p = [([next(it) for _ in cards], nn, pad, mn) for cards, nn, pad, mn in flat]
    stills, durs, segs, cues, total = schedule(beats_p, clips, cap)
    encode(stills, durs, segs, total, out_mp4, slug, cues, out_vtt)
    for p in paths: os.remove(p)
    return out_mp4, total

# ---------------- builders ----------------
def kick(item):
    f = item['fields']; return '%s \u00b7 %s \u00b7 %s' % (item['type'], (f.get('level') or '').upper(), (f.get('subject') or '').replace('-', ' '))

def cta_line(item):
    return {'lesson': 'The full lesson, with practice', 'reference': 'The full sheet, with every row', 'practice': 'Do the whole exercise yourself',
            'word': 'The word page, with a check', 'dialogue': 'The full dialogue and what to listen for', 'article': 'Read the full article'}[item['type']]

def spoken_path(item):
    return 'The link is on screen.'

def build_lesson(item):
    f = item['fields']; k = kick(item); title = item['title']; tgt = clean(f.get('target_en'))
    ex = [clean(x) for x in (f.get('examples_en') or '').split('\n') if clean(x)][:4]
    expl = sentences(f.get('explanation'), 6); wo = sentences(f.get('watch_out'), 3)
    def beats():
        b = []
        b.append(([lambda p: paint_title(k, p, title, f.get('excerpt'))], [("Today's lesson: %s." % title, NARR)], 0.8, 3.0))
        b.append(([lambda p: paint_english(k, p, 'English, never translated', tgt)], [('Here is the sentence we are learning.', NARR), (tgt, SPEC), (tgt, SLOW)], 1.0, 4.0))
        for c in chunks(expl, 2):
            b.append(([lambda p, c=c: paint_prose(k, p, 'Explanation', c)], [(c, NARR)], 0.6, 3.0))
        if ex:
            b.append(([lambda p: paint_list(k, p, 'More examples', ex)], [('More examples.', NARR)] + [(e, SPEC) for e in ex], 0.8, 4.0))
        for j, c in enumerate(chunks(wo, 2)):
            b.append(([lambda p, c=c: paint_prose(k, p, 'Watch out', c)], ([('Watch out.', NARR)] if j == 0 else []) + [(c, NARR)], 0.6, 3.0))
        b.append(([lambda p: paint_cta(k, tgt, item['slug'], cta_line(item))], [('Say it today:', NARR), (tgt, SPEC), ('%s is on %s. %s' % (cta_line(item), SITE.replace('.co', ' dot co'), spoken_path(item)), NARR)], 1.2, 5.0))
        return b
    return beats

def build_word(item):
    f = item['fields']; k = kick(item); title = item['title']; hw = clean(f.get('word_headword')); pos = clean(f.get('word_pos'))
    ex = [clean(x) for x in (f.get('word_examples_en') or '').split('\n') if clean(x)][:3]
    col = [clean(x) for x in (f.get('word_collocations_en') or '').split('\n') if clean(x)][:5]
    note = sentences(f.get('word_usage_note'), 5)
    chk = clean((f.get('word_check_item_en') or '').split('\n')[0]); ans = clean(f.get('word_check_answer_en'))
    chk = re.sub(r'^\d+\.\s*', '', chk)
    def beats():
        b = []
        b.append(([lambda p: paint_title(k, p, title, 'Word of the day')], [('Word of the day. The meaning: %s.' % title, NARR)], 0.8, 3.0))
        b.append(([lambda p: paint_english(k, p, 'The word', hw, note=pos)], [('The word is:', NARR), (hw, SPEC), (hw, SLOW), ('%s.' % pos, NARR)], 1.0, 4.0))
        for c in chunks(note, 2):
            b.append(([lambda p, c=c: paint_prose(k, p, 'When to use it', c)], [(c, NARR)], 0.6, 3.0))
        if ex:
            b.append(([lambda p: paint_list(k, p, 'In a sentence', ex)], [('In a sentence.', NARR)] + [(e, SPEC) for e in ex], 0.8, 4.0))
        if col:
            b.append(([lambda p: paint_list(k, p, 'It goes with', col)], [('It goes with:', NARR)] + [(c, SPEC) for c in col], 0.8, 4.0))
        if chk and ans and '___' in chk:
            filled = chk.replace('___', ans)
            b.append(([lambda p: paint_english(k, p, 'Your turn', chk), lambda p: paint_english(k, p, 'Your turn', filled, tone='ok', hi=set(norm(w) for w in ans.split()))],
                      [('Your turn. %s' % speakable(chk), NARR), (filled, SPEC)], 2.6, 8.0))
        b.append(([lambda p: paint_cta(k, hw, item['slug'], cta_line(item))], [('Say it today:', NARR), (hw, SPEC), ('%s is on %s. %s' % (cta_line(item), SITE.replace('.co', ' dot co'), spoken_path(item)), NARR)], 1.2, 5.0))
        return b
    return beats

def table_rows(f):
    rows = []
    for tr in re.findall(r'<tr>(.*?)</tr>', f.get('table_rows') or '', flags=re.S):
        td = [clean(strip_tags(x)) for x in re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.S)]
        td = [x for x in td if x]
        if len(td) >= 2: rows.append((td[0], td[1], td[2:]))
    return rows

def build_reference(item):
    f = item['fields']; k = kick(item); title = item['title']
    tgt = [clean(x) for x in re.split(r'\s+-\s+|\s+/\s+', clean(f.get('target_en'))) if clean(x)][:4]
    expl = sentences(f.get('explanation'), 4); rows = table_rows(f)
    ex = [clean(x) for x in (f.get('examples_en') or '').split('\n') if clean(x)][:3]
    err = clean(f.get('error_en')); wo = sentences(f.get('watch_out'), 3)
    def beats(nrows=8, nexpl=4, nwo=3):
        b = []
        b.append(([lambda p: paint_title(k, p, title, f.get('excerpt'))], [('Reference sheet: %s.' % title, NARR)], 0.8, 3.0))
        if tgt:
            b.append(([lambda p: paint_list(k, p, 'English, never translated', tgt)], [('Listen to the contrast.', NARR)] + [(t, SPEC) for t in tgt], 0.8, 4.0))
        for c in chunks(expl[:nexpl], 2):
            b.append(([lambda p, c=c: paint_prose(k, p, 'How to use this sheet', c)], [(c, NARR)], 0.6, 3.0))
        use = rows[:nrows]
        for i in range(0, len(use), 2):
            pair = use[i:i + 2]
            lines = [('The table.', NARR)] if i == 0 else []
            for a, bb, _extra in pair: lines += [(a, SPEC), (bb, NARR)]
            b.append(([lambda p, pair=pair: paint_rows(k, p, 'From the table', pair)], lines, 0.5, 3.0))
        if ex:
            b.append(([lambda p: paint_list(k, p, 'In use', ex)], [('In use.', NARR)] + [(e, SPEC) for e in ex], 0.8, 4.0))
        if err and wo:
            b.append(([lambda p: paint_english(k, p, 'Watch out', err, tone='bad')], [('Watch out. This one is wrong:', NARR), (err, SPEC), (' '.join(wo[:nwo]), NARR)], 0.8, 4.0))
        take = tgt[0] if tgt else (ex[0] if ex else title)
        b.append(([lambda p: paint_cta(k, take, item['slug'], cta_line(item))], [('Keep this one:', NARR), (take, SPEC), ('%s is on %s. %s' % (cta_line(item), SITE.replace('.co', ' dot co'), spoken_path(item)), NARR)], 1.2, 5.0))
        return b
    return beats

def build_practice(item):
    f = item['fields']; k = kick(item); title = item['title']
    focus = [clean(x) for x in (f.get('focus_en') or '').split('\n') if clean(x)][:3]
    instr = sentences(f.get('instruction'), 3)
    def numbered(text):
        out = {}
        for m in re.finditer(r'(?m)^\s*(\d+)\.\s*(.+)$', text or ''): out[int(m.group(1))] = clean(m.group(2))
        return out
    items = numbered(f.get('items_en')); answers = numbered(f.get('answers_en'))
    note = sentences(f.get('answer_note'), 5)
    keys = [n for n in sorted(items) if n in answers and '___' in items[n]][:5]
    def beats():
        b = []
        b.append(([lambda p: paint_title(k, p, title, f.get('excerpt'))], [('Practice: %s.' % title, NARR)], 0.8, 3.0))
        if focus:
            b.append(([lambda p: paint_list(k, p, 'What you are practising', focus)], [('What you are practising.', NARR)] + [(x, SPEC) for x in focus], 0.8, 4.0))
        if instr:
            b.append(([lambda p: paint_prose(k, p, 'What to do', ' '.join(instr))], [(' '.join(instr), NARR)], 0.6, 3.0))
        for n in keys:
            q = items[n]; a = (answers[n] or '').strip()
            shown = re.sub(r'\s*\([^)]*\)\s*$', '', q)          # bracket hint shown separately as the note
            hint = re.search(r'\(([^)]*)\)', q)
            base = re.sub(r'\s*\([^)]*\)', '', q)
            if a.lower() in ('nothing', 'none', 'no word', 'zero', 'ø'):
                filled = re.sub(r'\s+', ' ', base.replace('___', '')).strip()
                hi = None
            else:
                filled = base.replace('___', a)
                hi = set(norm(w) for w in a.split())
            b.append(([lambda p, q=shown, h=hint, n=n: paint_english(k, p, 'Item %d' % n, q, note=(h.group(1) if h else None)),
                       lambda p, fl=filled, n=n, hi=hi: paint_english(k, p, 'Item %d' % n, fl, tone='ok', hi=hi)],
                      [(speakable(q), NARR), (filled, SPEC)], 2.8, 8.0))
        for j, c in enumerate(chunks(note, 2)):     # one card per two sentences: five sentences overflow a card
            b.append(([lambda p, c=c: paint_prose(k, p, 'Why', c)], ([('Why.', NARR)] if j == 0 else []) + [(c, NARR)], 0.6, 3.0))
        take = focus[0] if focus else title
        b.append(([lambda p: paint_cta(k, take, item['slug'], cta_line(item))], [('Say it today:', NARR), (take, SPEC), ('%s on %s. %s' % (cta_line(item), SITE.replace('.co', ' dot co'), spoken_path(item)), NARR)], 1.2, 5.0))
        return b
    return beats

def build_dialogue(item):
    f = item['fields']; k = kick(item); title = item['title']
    sit = sentences(f.get('situation'), 3)
    lines = []
    for m in re.finditer(r'(?m)^\s*\d+\.\s*([A-Z]):\s*(.+)$', f.get('dialogue_en') or ''): lines.append((m.group(1), clean(m.group(2))))
    speakers = []
    for who, _ in lines:
        if who not in speakers: speakers.append(who)
    voice = {who: DLG[i % len(DLG)] for i, who in enumerate(speakers)}
    lf = sentences(f.get('listen_for'), 4)
    chk = re.sub(r'^\d+\.\s*', '', clean((f.get('check_item_en') or '').split('\n')[0])); ans = re.sub(r'^\d+\.\s*', '', clean(f.get('check_answer_en')))
    def beats():
        b = []
        b.append(([lambda p: paint_title(k, p, title, f.get('excerpt'))], [('A dialogue: %s.' % title, NARR)], 0.8, 3.0))
        if sit:
            b.append(([lambda p: paint_prose(k, p, 'The situation', ' '.join(sit))], [(' '.join(sit), NARR)], 0.6, 3.0))
        n = len(lines)
        for i, (who, text) in enumerate(lines, 1):
            b.append(([lambda p, who=who, text=text, i=i: paint_line(k, p, who, text, i, n)], [(text, voice[who])], 0.35, 1.5))
        for j, c in enumerate(chunks(lf, 2)):
            b.append(([lambda p, c=c: paint_prose(k, p, 'Listen for', c)], ([('Listen for this.', NARR)] if j == 0 else []) + [(c, NARR)], 0.6, 3.0))
        if chk and ans and '___' in chk:
            filled = chk.replace('___', ans)
            b.append(([lambda p: paint_english(k, p, 'Check yourself', chk), lambda p: paint_english(k, p, 'Check yourself', filled, tone='ok', hi=set(norm(w) for w in ans.split()))],
                      [('Check yourself. %s' % speakable(chk), NARR), (filled, SPEC)], 2.6, 8.0))
        take = lines[0][1] if lines else title
        b.append(([lambda p: paint_cta(k, take, item['slug'], cta_line(item))], [('%s is on %s. %s' % (cta_line(item), SITE.replace('.co', ' dot co'), spoken_path(item)), NARR)], 1.2, 5.0))
        return b
    return beats

def build_article(item):
    f = item['fields']; k = 'article \u00b7 read'; title = item['title']
    body = f.get('body') or ''
    # sections: h2 + the first paragraph that follows
    secs = []
    for m in re.finditer(r'<h2[^>]*>(.*?)</h2>(.*?)(?=<h2|\Z)', body, flags=re.S):
        h = clean(strip_tags(m.group(1))); ps = re.findall(r'<p[^>]*>(.*?)</p>', m.group(2), flags=re.S)
        txt = ' '.join(sentences(clean(strip_tags(ps[0])), 2)) if ps else ''
        if h and txt: secs.append((h, txt))
    secs = secs[:4]
    intro = re.findall(r'<p[^>]*>(.*?)</p>', body.split('<h2')[0], flags=re.S)
    lede = ' '.join(sentences(clean(strip_tags(intro[0])), 2)) if intro else clean(f.get('excerpt'))
    def beats():
        b = []
        b.append(([lambda p: paint_title(k, p, title, f.get('excerpt'))], [('From the blog: %s.' % title, NARR)], 0.8, 3.0))
        if lede:
            b.append(([lambda p: paint_prose(k, p, 'The idea', lede)], [(lede, NARR)], 0.6, 3.0))
        for h, txt in secs:
            b.append(([lambda p, h=h, txt=txt: paint_prose(k, p, h, txt)], [('%s.' % h, NARR), (txt, NARR)], 0.6, 3.0))
        b.append(([lambda p: paint_cta(k, f.get('excerpt') or title, item['slug'], cta_line(item))], [('%s on %s. %s' % (cta_line(item), SITE.replace('.co', ' dot co'), spoken_path(item)), NARR)], 1.2, 5.0))
        return b
    return beats

BUILDERS = {'lesson': build_lesson, 'word': build_word, 'reference': build_reference, 'practice': build_practice, 'dialogue': build_dialogue, 'article': build_article}

def render_item(item):
    slug = item['slug'].replace('/', '-')
    beats = BUILDERS[item['type']](item)
    try:
        return render(slug, beats, item['slug'])
    except RuntimeError as e:
        if 'over cap' not in str(e) or item['type'] != 'reference': raise
        return render(slug, lambda: beats(nrows=4, nexpl=3, nwo=2), item['slug'])

if __name__ == '__main__':
    items = json.load(io.open(os.path.join(HERE, 'items.json'), encoding='utf-8'))
    args = sys.argv[1:]
    if args and args[0] == '--all':
        typ = args[args.index('--type') + 1] if '--type' in args else None
        todo = [i for i in items if (not typ or i['type'] == typ)]
    else:
        todo = [i for i in items if i['slug'] in args or i['slug'].replace('/', '-') in args]
    state_p = os.path.join(HERE, 'render-state.json')
    state = json.load(open(state_p)) if os.path.exists(state_p) else {}
    for it in todo:
        slug = it['slug'].replace('/', '-')
        if state.get(slug, {}).get('ok') and os.path.exists(os.path.join(OUT, slug + '.mp4')) and '--force' not in args:
            continue
        t0 = time.time()
        try:
            mp4, total = render_item(it)
            state[slug] = {'ok': True, 'seconds': round(total, 1), 'bytes': os.path.getsize(mp4), 'type': it['type'], 'page': it['slug']}
            print('OK  %-60s %5.1fs  %.1f MB  (%.0fs)' % (slug, total, os.path.getsize(mp4) / 1e6, time.time() - t0), flush=True)
        except Exception as e:
            state[slug] = {'ok': False, 'error': str(e)[:300], 'type': it['type']}
            print('ERR %-60s %s' % (slug, str(e)[:200]), flush=True)
        json.dump(state, open(state_p, 'w'), indent=1)

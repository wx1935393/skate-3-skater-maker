#!/usr/bin/env python3
"""
skater_tool.py - inspect / patch Skate 3 (PS3) SKATER.P files.

Reverse-engineered from two samples (a modded skater and a stock skater).

CONFIRMED file layout (both samples)
  total size      : 0x280004 bytes (2,621,444), tail zero-padded
  0x000 - 0x003   : CRC-32/BZIP2 (poly 04C11DB7, init FFFFFFFF, xorout FFFFFFFF,
                    non-reflected), stored big-endian, computed over bytes
                    0x000004 .. 0x27FFFF  (everything after the CRC up to 0x280000)
  0x014           : 59 F2 D8 9B  (same in both files - type/version hash)
  0x01C           : big-endian length of the used payload (changes when the
                    amount of data changes, e.g. 0x1D7BA0 modded vs 0x1D7714 stock)
  body            : big-endian stream of length-prefixed fields, containing
                    5 repeated "cas_db" blocks (gear/material lists per slot),
                    15 embedded 160x320 JPEG thumbnails (3 per block), and
                    user-content URLs (.../PS3/LOGO/0148/1740948/<8-digit id>).

Rule of thumb
  * SAME-LENGTH edits (change a byte/flag, swap a LOGO id, swap a hash)
      -> patch bytes, then recompute CRC. Nothing else needed.
  * LENGTH-CHANGING edits (adding/removing gear entries, replacing a JPEG with
      a different-sized one) -> nested sizes/length fields must also be fixed.
      This tool does NOT do that; copy whole blocks from a donor file instead.

Usage
  python3 skater_tool.py info   SKATER.P
  python3 skater_tool.py verify SKATER.P
  python3 skater_tool.py fix    SKATER.P  [-o out.P]
  python3 skater_tool.py diff   A.P B.P
  python3 skater_tool.py patch  SKATER.P  0x122e 00  [-o out.P]
  python3 skater_tool.py logos  SKATER.P
  python3 skater_tool.py setlogo SKATER.P 89413371 12345678 [-o out.P]
  python3 skater_tool.py slots  SKATER.P
  python3 skater_tool.py delslot SKATER.P 1 Rostral [-o out.P]   (delete a gear slot from block 1-5)
  python3 skater_tool.py graphics SKATER.P [outdir]   (list / export embedded custom graphics as PNG)
  python3 skater_tool.py fillgraphic SKATER.P 89204405 ff0000 [-o out.P]   (solid-colour test)
  python3 skater_tool.py setgraphic SKATER.P 89204405 picture.png [--fit stretch|fit|fill] [--aspect 2.5] [--shift 0] [--sharpen 1.0] [--tone 8] [-o out.P]   (replace a graphic with an image; resized to 256x128)
  python3 skater_tool.py tints SKATER.P                       (list the colour table of every block)
  python3 skater_tool.py settint SKATER.P 1 Pants ff0000 [--field A|B|AB] [--glow 6 | --neon 20] [-o out.P]   (block 1-5, slot name or entry number)
  python3 skater_tool.py thumbs SKATER.P outdir
Always keep a backup of the original save.
"""
import argparse, os, re, struct, sys

CRC_START, CRC_END, FILE_SIZE = 4, 0x280000, 0x280004
GEAR_NAMES = [b"Feet", b"Glasses", b"Hair", b"InnerTorso", b"Organ", b"OuterTorso",
              b"Pants", b"Rostral", b"SkateBoard", b"SkateTruck", b"SkateWheel",
              b"Hat", b"Gloves"]

def _table():
    t = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if c & 0x80000000 else (c << 1) & 0xFFFFFFFF
        t.append(c)
    return t
_T = _table()

def crc32_bzip2(data: bytes) -> int:
    try:  # fast path if a C implementation exists
        import crcmod  # noqa
    except Exception:
        pass
    c = 0xFFFFFFFF
    T = _T
    for b in data:
        c = T[((c >> 24) ^ b) & 0xFF] ^ ((c << 8) & 0xFFFFFFFF)
    return c ^ 0xFFFFFFFF

def load(path):
    d = bytearray(open(path, "rb").read())
    if len(d) != FILE_SIZE:
        print(f"warning: size is {len(d):#x}, expected {FILE_SIZE:#x}", file=sys.stderr)
    return d

def stored_crc(d): return struct.unpack(">I", d[:4])[0]
def calc_crc(d):   return crc32_bzip2(bytes(d[CRC_START:CRC_END]))

def fix_crc(d):
    d[:4] = struct.pack(">I", calc_crc(d))
    return d

def save(d, path):
    open(path, "wb").write(bytes(d))

def out_path(args, default_suffix=".fixed"):
    return args.o or (args.file + default_suffix)

# ------------------------------------------------------------------ commands

# ------------------------------------------------------------------ slot parsing / deletion
# Layout of one cas_db block (all big-endian), worked out from real saves:
#   [E:4][8][F:4][0xc][F:4][7][6] "cas_db"  [3][0x19][2][nslots]  slot*nslots  tint-table ...
#   slot = len+name, [1], hash(8), nitems(4), item*nitems
#   item = flag(1) id(8) v1(8) [c1][c2] v2(8) nchan(4) chan*nchan ; chan = len+name, id(8)
# Size fields that cover a slot (all shrink by the number of bytes removed):
#   E  @ block-28 : distance to the next cas_db minus 9 (one per block)
#   F  @ block-12 and block-20 : distance to first thumbnail JPEG minus 0x65
#   G  @ 0x29b0 and H @ 0x29c0 : cover all 5 blocks (block 1 only)
#   payload length @ 0x1c
def _u(d, o): return struct.unpack(">I", d[o:o+4])[0]

def parse_block(d, b):
    p = b + 6 + 16
    n = _u(d, b + 6 + 12); slots = []
    for _ in range(n):
        s = p; L = _u(d, p); name = bytes(d[p+4:p+4+L]).decode("ascii"); p += 4 + L + 4 + 8
        ni = _u(d, p); p += 4
        for _ in range(ni):
            p += 1 + 8 + 8 + 8 + 8
            nc = _u(d, p); p += 4
            for _ in range(nc):
                L = _u(d, p); p += 4 + L + 8
        slots.append((name, s, p))
    return slots

def list_slots(d):
    out = []
    for b in blocks(d):
        try: out.append(parse_block(d, b))
        except Exception: out.append(None)
    return out

def delete_slot(d, block_no, name):
    """Remove one gear slot from block `block_no` (1-5) and fix every size field."""
    bl = blocks(d)
    if not 1 <= block_no <= len(bl): raise ValueError("bad block number")
    b = bl[block_no - 1]
    slots = parse_block(d, b)
    hit = [s for s in slots if s[0] == name]
    if not hit: raise ValueError(f"block {block_no} has no slot {name!r}")
    _, s, e = hit[0]; n = e - s
    old_len = len(d)
    del d[s:e]
    d[b+18:b+22] = struct.pack(">I", len(slots) - 1)
    for off in (b-28, b-20, b-12):
        d[off:off+4] = struct.pack(">I", _u(d, off) - n)
    for off in (0x29b0, 0x29c0):          # cover all blocks; start before block 1
        d[off:off+4] = struct.pack(">I", _u(d, off) - n)
    d[0x1c:0x20] = struct.pack(">I", _u(d, 0x1c) - n)
    d += bytes(old_len - len(d))          # keep file size; tail is zero padding
    fix_crc(d)
    return n

def check_sizes(d):
    """Return a list of problems with the size fields (empty = consistent)."""
    bad = []; bl = blocks(d)
    jp = [m.start() for m in re.finditer(rb"\xff\xd8\xff", bytes(d))]
    for i, b in enumerate(bl):
        if i + 1 < len(bl) and _u(d, b-28) != bl[i+1] - b - 9: bad.append(f"block {i+1}: E field")
        j = min(x for x in jp if x > b)
        if not (_u(d, b-12) == _u(d, b-20) == j - b - 0x65): bad.append(f"block {i+1}: F fields")
        try: parse_block(d, b)
        except Exception: bad.append(f"block {i+1}: slot list does not parse")
    if _u(d, 0x29b0) - _u(d, 0x29c0) != 0x12: bad.append("G/H fields")
    return bad

def cmd_slots(a):
    d = load(a.file)
    for i, sl in enumerate(list_slots(d)):
        print(f"block {i+1}: " + (", ".join(f"{n}({e-s}B)" for n, s, e in sl) if sl else "(cannot parse)"))

def cmd_delslot(a):
    d = load(a.file); n = delete_slot(d, int(a.block), a.slot)
    p = out_path(a, ".mod"); save(d, p)
    print(f"removed {a.slot} from block {a.block} ({n} bytes); size fields + CRC fixed; wrote {p}")
    bad = check_sizes(d); print("consistency check:", "OK" if not bad else bad)


# ------------------------------------------------------------------ embedded custom graphics (RW4ps3 textures)
# Each custom graphic is a 0xB000-byte "RW4ps3" blob holding one PS3 texture:
# 256x128, standard DXT5 (alpha block then colour block, little-endian), 9 mip levels,
# 0xAAD0 bytes of pixel data starting 0x238 bytes into the blob (the RW4 header stores 0x238 twice, at 0x144/0x148;
# in-game calibration with per-block colour codes confirmed the first block is read from there, NOT from 0x2B8).
# Header 0x15C..0x15F = 88 09 02 00 is the PS3 texture descriptor: DXT5, 9 mips. Do NOT lower the mip count: the game
# samples a low mip level for shirt/hat prints and they vanish if only level 0 exists.
GFX_BLOB, GFX_DATA, GFX_SIZE, GFX_W, GFX_H = 0xB000, 0x238, 0xAAD0, 256, 128
# STREAM_SHIFT is no longer needed (the old 16-block shift was compensating for a wrong GFX_DATA). 0 = none.
# REV_COLOR / REV_ALPHA: in-game test (T8..T11) with the correct 0x238 start showed NO pixel reversal is needed:
# normal DXT5 order gives sharp text; the old 'reversed' finding came from the wrong start offset.
STREAM_SHIFT, REV_COLOR, REV_ALPHA = 0, False, False

def find_graphics(d):
    out = []
    for m in re.finditer(rb"\x89RW4ps3", bytes(d)):
        s = m.start(); n = re.search(rb"(\d{8})\.Texture", bytes(d[s:s + GFX_BLOB]))
        if n: out.append((n.group(1).decode(), s))
    return out

def _c565(c): return ((c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31)
def _p565(r, g, b): return ((r * 31 + 127) // 255 << 11) | ((g * 63 + 127) // 255 << 5) | ((b * 31 + 127) // 255)

def decode_graphic(d, start, w=GFX_W, h=GFX_H):
    """Decode the top mip level -> (w, h, RGBA bytes)."""
    px = bytearray(w * h * 4); p = start + GFX_DATA - STREAM_SHIFT * 16
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            a0, a1 = d[p], d[p+1]; ab = int.from_bytes(d[p+2:p+8], "little")
            c0, c1, cb = struct.unpack("<HHI", d[p+8:p+16]); p += 16
            A, B = _c565(c0), _c565(c1)
            pal = [A, B, tuple((2*A[i]+B[i])//3 for i in range(3)), tuple((A[i]+2*B[i])//3 for i in range(3))]
            if a0 > a1: al = [a0, a1] + [((7-i)*a0+i*a1)//7 for i in range(1, 7)]
            else: al = [a0, a1] + [((5-i)*a0+i*a1)//5 for i in range(1, 5)] + [0, 255]
            for k in range(16):
                o = ((by + k // 4) * w + bx + k % 4) * 4
                kc = 15 - k if REV_COLOR else k; ka = 15 - k if REV_ALPHA else k
                px[o:o+3] = bytes(pal[(cb >> 2*kc) & 3]); px[o+3] = al[(ab >> 3*ka) & 7]
    return w, h, bytes(px)

def _enc_block(pix):
    """pix: 16 (r,g,b,a) tuples -> 16-byte DXT5 block (principal-axis colour fit, 8-step alpha)."""
    pa = pix[::-1] if REV_ALPHA else pix; pix = pix[::-1] if REV_COLOR else pix
    al = [p[3] for p in pa]; a0, a1 = max(al), min(al)
    if a0 == a1: ai = 0
    else:
        tab = [a0, a1] + [((7-i)*a0+i*a1)//7 for i in range(1, 7)]
        ai = 0
        for k, v in enumerate(al): ai |= min(range(8), key=lambda j: abs(tab[j]-v)) << 3*k
    # colour: ignore fully transparent pixels when fitting (they are invisible)
    vis = [p for p in pix if p[3] > 8] or pix
    n = len(vis); mean = [sum(p[i] for p in vis) / n for i in range(3)]
    if all(max(p[i] for p in vis) - min(p[i] for p in vis) < 2 for i in range(3)):
        c0 = c1 = _p565(*[int(round(m)) for m in mean]); ci = 0
    else:
        cov = [[sum((p[i]-mean[i])*(p[j]-mean[j]) for p in vis) for j in range(3)] for i in range(3)]
        ax = [0.57735, 0.57735, 0.57735]
        for _ in range(8):
            v = [sum(cov[i][j]*ax[j] for j in range(3)) for i in range(3)]
            m = max(abs(x) for x in v) or 1.0; ax = [x/m for x in v]
        t = [sum((p[i]-mean[i])*ax[i] for i in range(3)) for p in vis]
        tmin, tmax = min(t), max(t)
        clamp = lambda x: max(0, min(255, int(round(x))))
        e0 = [clamp(mean[i] + ax[i]*tmax) for i in range(3)]; e1 = [clamp(mean[i] + ax[i]*tmin) for i in range(3)]
        c0, c1 = _p565(*e0), _p565(*e1)
        if c0 < c1: c0, c1 = c1, c0
        if c0 == c1: ci = 0
        else:
            A, B = _c565(c0), _c565(c1)
            pal = [A, B, tuple((2*A[i]+B[i])//3 for i in range(3)), tuple((A[i]+2*B[i])//3 for i in range(3))]
            ci = 0
            for k, p in enumerate(pix):
                ci |= min(range(4), key=lambda j: sum((pal[j][i]-p[i])**2 for i in range(3))) << 2*k
    return bytes([a0, a1]) + ai.to_bytes(6, "little") + struct.pack("<HHI", c0, c1, ci)

# Tone curve. Measured in game on the shirt (RAMP_levels test): the print is brightened roughly like
#   shown = 1 - exp(-TONE_K * stored)      (TONE_K ~ 8: stored 17/255 -> 128, 51 -> 207, 102 -> 252, >=119 -> 255)
# so everything above ~45 % stored brightness looks white. pre_tone() applies the inverse so the print shows the
# intended colours. Limited by 5/6-bit DXT endpoints in the dark range (posterised shadows).
import math
def pre_tone(img, k):
    """Darken an RGBA image so that, after the game's brightening (k), it looks like the original. k <= 0: no change."""
    if k <= 0: return img
    lut = [min(255, round(-math.log(1 - min(v / 255, 0.9995)) / k * 255)) for v in range(256)]
    r, g, b, a = img.split()
    from PIL import Image
    return Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))

def prepare_image(img, fit="stretch", aspect=2.0, shift=0):
    """Return a 256x128 RGBA image.
    fit: stretch | fit (keep ratio, transparent bars) | fill (keep ratio, crop).
    aspect: width/height of the area the game paints the texture onto (2.0 = undistorted; ~2.6 measured on the shirt front).
    shift: texture columns (px) by which the game's visible window is offset; content is rolled left by this amount."""
    from PIL import Image, ImageChops
    img = img.convert("RGBA")
    if fit == "stretch":
        out = img.convert("RGBa").resize((GFX_W, GFX_H), Image.LANCZOS).convert("RGBA")
    else:
        WW = max(1, round(aspect * GFX_H))                      # "world" canvas: what the viewer actually sees
        w, h = img.size; k = (min if fit == "fit" else max)(WW / w, GFX_H / h)
        nw, nh = max(1, round(w * k)), max(1, round(h * k))
        r = img.convert("RGBa").resize((nw, nh), Image.LANCZOS).convert("RGBA")
        world = Image.new("RGBA", (WW, GFX_H), (0, 0, 0, 0)); world.paste(r, ((WW - nw) // 2, (GFX_H - nh) // 2))
        out = world.convert("RGBa").resize((GFX_W, GFX_H), Image.LANCZOS).convert("RGBA")   # squeeze into texture space
    if shift % GFX_W: out = ImageChops.offset(out, -(shift % GFX_W), 0)
    return out

def encode_graphic(img, fit="stretch", aspect=2.0, shift=0, sharpen=0.0, tone=0.0):
    """PIL image -> 0xAAD0 bytes (DXT5, 9 mips, 256x128). Mips are alpha-weighted (no dark halos).
    sharpen > 0: mips 1..8 are rebuilt with Lanczos + unsharp mask (the game shows shirt/hat prints from a low mip, which is soft)."""
    from PIL import Image, ImageFilter
    top = pre_tone(prepare_image(img, fit, aspect, shift), tone).convert("RGBa")
    out = bytearray(); w, h = GFX_W, GFX_H
    for lvl in range(9):
        if lvl == 0: im = top.convert("RGBA")
        elif sharpen > 0:
            im = top.resize((w, h), Image.LANCZOS).convert("RGBA"); r, g, b, a = im.split()
            rgb = Image.merge("RGB", (r, g, b)).filter(ImageFilter.UnsharpMask(radius=1.0, percent=int(150 * sharpen), threshold=0))
            im = Image.merge("RGBA", (*rgb.split(), a))
        else: im = top.resize((w, h), Image.BOX).convert("RGBA")
        px = im.load(); bw, bh = max(w // 4, 1), max(h // 4, 1)
        for by in range(bh):
            for bx in range(bw):
                out += _enc_block([px[min(bx*4 + k % 4, w-1), min(by*4 + k // 4, h-1)] for k in range(16)])
        w, h = max(w // 2, 1), max(h // 2, 1)
    assert len(out) == GFX_SIZE, len(out)
    return bytes(out[STREAM_SHIFT * 16:]) + bytes(STREAM_SHIFT * 16)   # rotate so the game's early read lines up

def put_graphic(d, gid, data):
    hit = [s for i, s in find_graphics(d) if i == gid]
    if not hit: raise ValueError(f"graphic {gid} not embedded in this save")
    s = hit[0] + GFX_DATA; d[s:s + GFX_SIZE] = data; fix_crc(d)

def fill_graphic(d, gid, r, g, b, a=255):
    """Overwrite a graphic with one solid colour (all mips)."""
    c = _p565(r, g, b)
    put_graphic(d, gid, (bytes([a, a]) + bytes(6) + struct.pack("<HHI", c, c, 0)) * (GFX_SIZE // 16))

# ------------------------------------------------------------------ colour (tint) table
# After the slot list of every cas_db block comes a table of 13 entries x 44 bytes (header 18 bytes, count at +10):
#   A: 3 x float32 BE   B: 3 x float32 BE   hash: 8 bytes   id: 8 bytes   next-index: 4 bytes
# In the live skater (block 1) the hash equals the gear slot's header hash (Hat, Hair, SkateBoard, OuterTorso, Feet, Pants).
# Values are linear floats and may exceed 1.0 (white shirt A = 2.5) or be negative (the cyan items are A = -9, 3, 5, which
# clamps to cyan). A is believed to be the main colour (B secondary) - to be confirmed in game with TINT_* test saves.
TINT_STRIDE, TINT_HDR = 44, 18
ROLE_GUESS = {0: "Hat", 1: "Hair", 2: "Face/Rostral", 3: "Skin", 4: "Skin", 5: "Skin", 6: "Organ/Board", 7: "Hair (2)",
              8: "Hair (3)", 9: "OuterTorso", 10: "Feet", 11: "?", 12: "Pants"}

TINT_SIG = re.compile(rb"\x06\x01\x00\x00\x00\x0d\x00\x00\x00\x0d\x00\x00\x00\x00")

def tint_tables(d, block_no):
    """Offsets of the first entry of every copy of the tint table belonging to block `block_no` (1-5).
    Each block carries TWO copies: one right after the slot list (used in game) and one at the end of the block's
    region (the skater editor reads this one: changing only the first copy does not show in the editor)."""
    bl = blocks(d); lo = bl[block_no - 1]; hi = bl[block_no] if block_no < len(bl) else CRC_END
    return [lo - 12 + m.start() + 14 for m in TINT_SIG.finditer(bytes(d[lo - 12:hi]))]

def tint_entries(d, block_no, copy=0):
    """-> list of dicts for block `block_no` (1-5): idx, off, A, B, hash, slot (name if its hash matches a gear slot, else None)."""
    b = blocks(d)[block_no - 1]; sl = parse_block(d, b)
    hashes = {bytes(d[s + 8 + _u(d, s):s + 16 + _u(d, s)]).hex(): n for n, s, e in sl}
    tabs = tint_tables(d, block_no)
    if not tabs: raise ValueError(f"block {block_no} has no colour table")
    base = tabs[copy]; out = []
    for k in range(13):
        o = base + k * TINT_STRIDE; h = bytes(d[o + 24:o + 32]).hex()
        out.append(dict(idx=k, off=o, A=struct.unpack(">3f", d[o:o + 12]), B=struct.unpack(">3f", d[o + 12:o + 24]),
                        hash=h, slot=hashes.get(h), guess=ROLE_GUESS.get(k, "?")))
    return out

def set_tint(d, block_no, idx, A=None, B=None):
    """Overwrite the A and/or B colour (3 floats) of entry idx in every copy of the block's table. Same length -> fix CRC only."""
    for base in tint_tables(d, block_no):
        o = base + idx * TINT_STRIDE
        if A is not None: d[o:o + 12] = struct.pack(">3f", *A)
        if B is not None: d[o + 12:o + 24] = struct.pack(">3f", *B)
    fix_crc(d)

NEON_STRENGTH = 20.0
def neon_rgb(h, strength=NEON_STRENGTH):
    """Glow look: each channel c in 0..1 -> (c-0.5)*2*strength, so 'on' channels go far above white and 'off' channels
    go negative (like the cyan board A=-9,5,9). White -> blown-out white, black -> black. Needs in-game confirmation."""
    h = h.lstrip("#"); return tuple((int(h[i:i + 2], 16) / 255 - 0.5) * 2 * strength for i in (0, 2, 4))

def hex_to_rgb(h, scale=1.0):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) / 255 * scale for i in (0, 2, 4))

def find_tint(d, block_no, slot):
    """Entry for a gear slot name (hash match first, then the role guess); None if absent."""
    es = tint_entries(d, block_no)
    for e in es:
        if e["slot"] == slot: return e
    for e in es:
        if e["guess"] == slot: return e
    return None

def cmd_tints(a):
    d = load(a.file)
    for bi in range(1, len(blocks(d)) + 1):
        try: es = tint_entries(d, bi)
        except Exception: print(f"block {bi}: (cannot parse)"); continue
        print(f"block {bi}:")
        for e in es:
            f = lambda v: "(" + ", ".join(f"{x:.3f}" for x in v) + ")"
            print(f"  [{e['idx']:2d}] {(e['slot'] or '~' + e['guess']):<14} A={f(e['A'])} B={f(e['B'])}")

def cmd_settint(a):
    d = load(a.file); e = find_tint(d, int(a.block), a.slot) if not a.slot.isdigit() else tint_entries(d, int(a.block))[int(a.slot)]
    if e is None: sys.exit(f"no colour entry for {a.slot!r} in block {a.block}")
    rgb = neon_rgb(a.color, a.neon) if a.neon else hex_to_rgb(a.color, a.scale * a.glow); fld = a.field.upper()
    set_tint(d, int(a.block), e["idx"], A=rgb if fld in ("A", "AB") else None, B=rgb if fld in ("B", "AB") else None)
    p = out_path(a, ".mod"); save(d, p); print(f"entry {e['idx']} ({e['slot'] or e['guess']}) field {fld} = {tuple(round(x, 3) for x in rgb)}; CRC fixed; wrote {p}")

def cmd_graphics(a):
    d = load(a.file)
    for i, s in find_graphics(d): print(f"  {i}  blob @ {s:#x}")
    if a.outdir:
        from PIL import Image
        os.makedirs(a.outdir, exist_ok=True)
        for i, s in find_graphics(d):
            w, h, px = decode_graphic(d, s)
            Image.frombytes("RGBA", (w, h), px).save(os.path.join(a.outdir, f"graphic_{i}.png"))
        print("wrote PNGs to", a.outdir)

def cmd_fillgraphic(a):
    d = load(a.file); h = a.color.lstrip("#")
    fill_graphic(d, a.id, int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) if len(h) == 8 else 255)
    p = out_path(a, ".mod"); save(d, p); print(f"graphic {a.id} filled with #{h}; CRC fixed; wrote {p}")

def cmd_setgraphic(a):
    from PIL import Image
    d = load(a.file); put_graphic(d, a.id, encode_graphic(Image.open(a.image), a.fit, a.aspect, a.shift, a.sharpen, a.tone))
    p = out_path(a, ".mod"); save(d, p); print(f"graphic {a.id} replaced with {a.image} (256x128, DXT5); CRC fixed; wrote {p}")

def cmd_verify(a):
    d = load(a.file)
    s, c = stored_crc(d), calc_crc(d)
    print(f"stored CRC {s:08x}  computed {c:08x}  -> {'OK' if s == c else 'MISMATCH'}")
    return 0 if s == c else 1

def cmd_fix(a):
    d = load(a.file); old = stored_crc(d); fix_crc(d)
    p = out_path(a); save(d, p)
    print(f"CRC {old:08x} -> {stored_crc(d):08x}; wrote {p}")

def blocks(d):
    return sorted(m.start() for m in re.finditer(b"cas_db", d))

def cmd_info(a):
    d = load(a.file)
    s, c = stored_crc(d), calc_crc(d)
    print(f"file: {a.file}  size {len(d):#x}")
    print(f"CRC stored {s:08x} computed {c:08x} ({'OK' if s == c else 'MISMATCH'})")
    print(f"payload length field @0x1c: {struct.unpack('>I', d[0x1c:0x20])[0]:#x}")
    bl = blocks(d)
    print(f"{len(bl)} cas_db blocks at {[hex(x) for x in bl]}")
    for i, b in enumerate(bl):
        seg = bytes(d[b:b + 0x1800])
        found = [(seg.find(n), n.decode()) for n in GEAR_NAMES if n in seg]
        print(f"  block {i+1}: " + ", ".join(n for _, n in sorted(found)))
    n_jpg = len(re.findall(rb"\xff\xd8\xff", bytes(d)))
    print(f"JPEG thumbnails: {n_jpg}")
    cmd_logos(a, d)

def cmd_logos(a, d=None):
    d = d or load(a.file)
    ids = [(m.start(), m.group(3).decode()) for m in
           re.finditer(rb"LOGO/(\d{4})/(\d{7})/(\d{8})", bytes(d))]
    print(f"LOGO references ({len(ids)}):")
    for off, i in ids:
        print(f"  {off:#08x}  {i}")

def cmd_setlogo(a):
    d = load(a.file)
    old, new = a.old.encode(), a.new.encode()
    if len(old) != len(new):
        sys.exit("ids must have the same number of digits (length-changing edits unsupported)")
    n = bytes(d).count(old)
    if not n: sys.exit("old id not found")
    data = bytes(d).replace(old, new); d[:] = data
    fix_crc(d); p = out_path(a, ".mod"); save(d, p)
    print(f"replaced {n} occurrence(s); CRC fixed; wrote {p}")

def cmd_patch(a):
    d = load(a.file)
    off = int(a.offset, 0); val = bytes.fromhex(a.hex)
    if off < CRC_START or off + len(val) > CRC_END:
        sys.exit("offset outside checksummed range")
    print(f"{off:#x}: {bytes(d[off:off+len(val)]).hex()} -> {val.hex()}")
    d[off:off + len(val)] = val
    fix_crc(d); p = out_path(a, ".mod"); save(d, p)
    print(f"CRC fixed; wrote {p}")

def cmd_diff(a):
    x, y = load(a.a), load(a.b)
    print(f"payload len field: {struct.unpack('>I', x[0x1c:0x20])[0]:#x} vs {struct.unpack('>I', y[0x1c:0x20])[0]:#x}")
    head = [i for i in range(0x20, 0x29b2) if x[i] != y[i]]
    print("differing bytes before first cas_db block:",
          [(hex(i), f"{x[i]:02x}", f"{y[i]:02x}") for i in head])
    for name, d in (("A", x), ("B", y)):
        print(f"{name}: blocks {[hex(b) for b in blocks(d)]}")
        for i, b in enumerate(blocks(d)):
            seg = bytes(d[b:b + 0x1800])
            print(f"   block {i+1}: " + ", ".join(n.decode() for n in GEAR_NAMES if n in seg))
    print("A logos:", sorted(set(re.findall(rb'LOGO/\d{4}/\d{7}/(\d{8})', bytes(x)))))
    print("B logos:", sorted(set(re.findall(rb'LOGO/\d{4}/\d{7}/(\d{8})', bytes(y)))))

def cmd_thumbs(a):
    from PIL import Image
    import io
    d = bytes(load(a.file)); os.makedirs(a.outdir, exist_ok=True)
    for i, m in enumerate(re.finditer(rb"\xff\xd8\xff", d)):
        s = m.start(); e = d.find(b"\xff\xd9", s)
        while e != -1:
            try:
                im = Image.open(io.BytesIO(d[s:e + 2])); im.load(); break
            except Exception:
                e = d.find(b"\xff\xd9", e + 2)
        if e != -1:
            im.save(os.path.join(a.outdir, f"thumb_{i:02d}_{s:06x}.png"))
    print("done")

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    for n, f in (("info", cmd_info), ("verify", cmd_verify), ("fix", cmd_fix), ("logos", cmd_logos)):
        s = sp.add_parser(n); s.add_argument("file"); s.add_argument("-o"); s.set_defaults(fn=f)
    s = sp.add_parser("diff"); s.add_argument("a"); s.add_argument("b"); s.set_defaults(fn=cmd_diff)
    s = sp.add_parser("patch"); s.add_argument("file"); s.add_argument("offset"); s.add_argument("hex"); s.add_argument("-o"); s.set_defaults(fn=cmd_patch)
    s = sp.add_parser("setlogo"); s.add_argument("file"); s.add_argument("old"); s.add_argument("new"); s.add_argument("-o"); s.set_defaults(fn=cmd_setlogo)
    s = sp.add_parser("slots"); s.add_argument("file"); s.set_defaults(fn=cmd_slots)
    s = sp.add_parser("delslot"); s.add_argument("file"); s.add_argument("block"); s.add_argument("slot"); s.add_argument("-o"); s.set_defaults(fn=cmd_delslot)
    s = sp.add_parser("graphics"); s.add_argument("file"); s.add_argument("outdir", nargs="?"); s.set_defaults(fn=cmd_graphics)
    s = sp.add_parser("fillgraphic"); s.add_argument("file"); s.add_argument("id"); s.add_argument("color", help="RRGGBB or RRGGBBAA"); s.add_argument("-o"); s.set_defaults(fn=cmd_fillgraphic)
    s = sp.add_parser("setgraphic"); s.add_argument("file"); s.add_argument("id"); s.add_argument("image"); s.add_argument("--fit", choices=["stretch", "fit", "fill"], default="stretch"); s.add_argument("--aspect", type=float, default=2.0); s.add_argument("--shift", type=int, default=0); s.add_argument("--sharpen", type=float, default=0.0); s.add_argument("--tone", type=float, default=8.0, help="inverse tone curve strength (default 8 = measured on shirt, 0 = off)"); s.add_argument("-o"); s.set_defaults(fn=cmd_setgraphic)
    s = sp.add_parser("tints"); s.add_argument("file"); s.set_defaults(fn=cmd_tints)
    s = sp.add_parser("settint"); s.add_argument("file"); s.add_argument("block"); s.add_argument("slot"); s.add_argument("color", help="RRGGBB")
    s.add_argument("--field", default="A", choices=["A", "B", "AB", "a", "b", "ab"]); s.add_argument("--scale", type=float, default=1.0)
    s.add_argument("--neon", type=float, default=0.0, help="glow look: on-channels -> +N, off-channels -> -N (try 20)"); s.add_argument("--glow", type=float, default=1.0, help="HDR brightness multiplier (e.g. 4-8) to make the piece glow / bloom"); s.add_argument("-o"); s.set_defaults(fn=cmd_settint)
    s = sp.add_parser("thumbs"); s.add_argument("file"); s.add_argument("outdir"); s.set_defaults(fn=cmd_thumbs)
    a = p.parse_args()
    sys.exit(a.fn(a) or 0)

def _pause():
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass

def interactive(path=None):
    """Double-click / drag-and-drop mode: simple menu, window stays open."""
    class A: pass
    try:
        if not path:
            path = input("Drag a SKATER.P file onto this window (or type its path): ").strip().strip('"')
        while True:
            print("\n  1) info    2) verify CRC    3) fix CRC    4) list logos")
            print("  5) set logo id    6) export thumbnails    q) quit")
            c = input("> ").strip().lower()
            a = A(); a.file = path; a.o = None
            try:
                if c == "1": cmd_info(a)
                elif c == "2": cmd_verify(a)
                elif c == "3": cmd_fix(a)
                elif c == "4": cmd_logos(a)
                elif c == "5":
                    a.old = input("old 8-digit id: ").strip()
                    a.new = input("new 8-digit id: ").strip()
                    cmd_setlogo(a)
                elif c == "6":
                    a.outdir = os.path.join(os.path.dirname(os.path.abspath(path)), "thumbs")
                    cmd_thumbs(a)
                elif c in ("q", ""): break
            except SystemExit as e:
                print(e)
            except Exception as e:
                print("error:", e)
    except (KeyboardInterrupt, EOFError):
        pass

if __name__ == "__main__":
    if len(sys.argv) == 1:
        interactive(); 
    elif len(sys.argv) == 2 and os.path.isfile(sys.argv[1]):
        interactive(sys.argv[1])
    else:
        try:
            main()
        except SystemExit as e:
            if e.code not in (0, None):
                _pause()
            raise

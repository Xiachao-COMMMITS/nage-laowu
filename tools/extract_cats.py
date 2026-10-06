# -*- coding: utf-8 -*-
"""猫图预处理：去白底（保留内部白色）→ 头身分层 PNG + 元数据。

用法:
  python tools/extract_cats.py analyze   # 去白底 + 生成网格预览（标注头部用）
  python tools/extract_cats.py cut       # 按 HEAD_CFG 切割 head/body
"""
import json
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "public" / "assets" / "cats"

CATS = {
    "cat1": ROOT / "微信图片_20261004134819_109_13.jpg",  # 长条橘猫
    "cat2": ROOT / "微信图片_20261004134958_113_13.jpg",  # 三花
    "cat3": ROOT / "微信图片_20261004135112_117_13.jpg",  # 炸毛橘猫
}

WHITE_T = 232  # 白底阈值
SEAL_R = 6     # 轮廓断口封堵半径（形态学闭运算），防止白毛被误当背景

# 头身切割配置（analyze 后按网格预览标注，坐标为裁剪后 PNG 上的像素）
# head_box: (x0, y0, x1, y1) 头部包围盒；pivot: (x, y) 颈部旋转中心；facing: 头朝向
HEAD_CFG = {
    "cat1": {"head_box": (60, 5, 420, 360), "pivot": (270, 210), "facing": "left"},
    "cat2": {"head_box": (0, 230, 430, 630), "pivot": (249, 429), "facing": "left"},
    "cat3": {"head_box": (600, 30, 913, 420), "pivot": (729, 225), "facing": "right"},
}

# 头身切割羽化半径（很小，保证静止时接缝不可见）
FEATHER = 3


def exterior_whites(img):
    """flood fill：返回 bytearray，1=背景像素（将变透明），0=保留像素。

    难点：三花的白色毛发与白色背景同色。若猫轮廓存在细小断口，
    flood fill 会顺着断口钻进猫身，把整块白毛（四肢、胸毛）当背景删掉。
    这里先用形态学闭运算把断口封住再做 flood fill，
    于是被轮廓圈住的白毛会被保留为白色，而不是被扣成透明。
    """
    w, h = img.size
    r, g, b = img.convert("RGB").split()
    mk = lambda ch: ch.point(lambda v: 255 if v >= WHITE_T else 0)
    white = ImageChops.multiply(ImageChops.multiply(mk(r), mk(g)), mk(b))
    barrier = ImageChops.invert(white)
    k = SEAL_R * 2 + 1
    barrier = barrier.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))

    wb, bb = white.tobytes(), barrier.tobytes()
    seen = bytearray(w * h)
    q = deque()

    def push(x, y):
        i = y * w + x
        if wb[i] and not bb[i] and not seen[i]:
            seen[i] = 1
            q.append(i)

    for x in range(w):
        push(x, 0); push(x, h - 1)
    for y in range(h):
        push(0, y); push(w - 1, y)
    while q:
        i = q.popleft()
        x, y = i % w, i // w
        for nx, ny in ((x+1, y), (x-1, y), (x, y+1), (x, y-1)):
            if 0 <= nx < w and 0 <= ny < h:
                j = ny * w + nx
                if wb[j] and not bb[j] and not seen[j]:
                    seen[j] = 1
                    q.append(j)
    return seen


def remove_bg(name, src):
    img = Image.open(src).convert("RGBA")
    bg = exterior_whites(img)
    w, h = img.size
    px = img.load()
    for y in range(h):
        base = y * w
        for x in range(w):
            if bg[base + x]:
                px[x, y] = (255, 255, 255, 0)
    alpha = img.getchannel("A").filter(ImageFilter.GaussianBlur(1.2))
    img.putalpha(alpha)
    bbox = img.getchannel("A").getbbox()
    img = img.crop(bbox)
    img.save(OUT / f"{name}_full.png")
    print(f"{name}: {img.size} (cropped from {Image.open(src).size})")
    return img


def grid_preview(img, name, step=100):
    bg = Image.new("RGBA", img.size, (200, 200, 210, 255))
    bg.alpha_composite(img)
    d = ImageDraw.Draw(bg)
    w, h = img.size
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=(255, 0, 0, 120), width=1)
        d.text((x + 3, 3), str(x), fill=(255, 0, 0))
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=(0, 0, 255, 120), width=1)
        d.text((3, y + 3), str(y), fill=(0, 0, 255))
    cfg = HEAD_CFG.get(name)
    if cfg:
        d.rectangle(cfg["head_box"], outline=(0, 200, 0), width=3)
        px_, py_ = cfg["pivot"]
        d.ellipse([px_-8, py_-8, px_+8, py_+8], outline=(255, 0, 255), width=3)
    bg.convert("RGB").save(OUT / f"preview_{name}.jpg", quality=88)


def feathered_mask(size, box, feather=14):
    """头部区域掩码，边缘羽化。box 内=255，向外渐变。"""
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.rectangle(box, fill=255)
    return m.filter(ImageFilter.GaussianBlur(feather))


def fur_cover(img, box, blur=30):
    """按列取躯干毛色，铺满头部区域，作为歪头时露出的"身体背景"。

    返回 (RGB 毛色图, 遮罩)。只用于替换颜色，不碰 alpha，
    所以静止时与原图逐像素一致，歪头时露出的也是毛色而非背景。
    """
    w, h = img.size
    src = img.load()
    cut_y = int(box[3])
    cols = [None] * w
    for x in range(w):
        for y in range(cut_y, min(h, cut_y + 220)):
            r, g, b, a = src[x, y]
            if a > 200:
                cols[x] = (r, g, b)
                break
    # 取不到颜色的列，向左右借用最近的颜色
    last = None
    for x in range(w):
        if cols[x]:
            last = cols[x]
        elif last:
            cols[x] = last
    last = None
    for x in range(w - 1, -1, -1):
        if cols[x]:
            last = cols[x]
        elif last:
            cols[x] = last

    rgb = Image.new("RGB", img.size)
    rp = rgb.load()
    for x in range(w):
        c = cols[x] or (200, 200, 200)
        for y in range(h):
            rp[x, y] = c
    rgb = rgb.filter(ImageFilter.GaussianBlur(blur))

    rect = Image.new("L", img.size, 0)
    ImageDraw.Draw(rect).rectangle(box, fill=255)
    rect = rect.filter(ImageFilter.GaussianBlur(FEATHER))
    # 只在猫的不透明区域内替换颜色，轮廓边缘保持原样
    sil = img.getchannel("A").point(lambda v: 255 if v >= 250 else 0)
    return rgb, ImageChops.multiply(rect, sil)


def cut(name):
    img = Image.open(OUT / f"{name}_full.png").convert("RGBA")
    cfg = HEAD_CFG[name]
    box = cfg["head_box"]
    m = feathered_mask(img.size, box, FEATHER)
    a = img.getchannel("A")

    head = img.copy()
    head.putalpha(ImageChops.multiply(a, m))

    # 身体层：颜色换成毛色，alpha 按合成公式反解——
    # 内部实心区保持 img_a，轮廓处取 img_a*(1-m)，
    # 这样静止时 head 叠上去与原图逐像素一致，歪头时露出的又是实心毛色。
    rgb, color_mask = fur_cover(img, box)
    body = img.copy()
    body.paste(rgb, (0, 0), color_mask)

    interior = a.point(lambda v: 255 if v >= 250 else 0)
    factor = ImageChops.lighter(ImageChops.invert(m), interior)
    body.putalpha(ImageChops.multiply(a, factor))

    head.save(OUT / f"{name}_head.png")
    body.save(OUT / f"{name}_body.png")

    meta = {
        "w": img.size[0], "h": img.size[1],
        "pivot": cfg["pivot"],
        "head_box": cfg["head_box"],
        "facing": cfg["facing"],
    }
    (OUT / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    print(f"{name}: head/body cut, pivot={cfg['pivot']}")

    # 歪头 25° 合成预览（灰底：body 在下，head 绕 pivot 旋转）——仅供人工检查
    prev_dir = ROOT / "tools" / "preview"
    prev_dir.mkdir(exist_ok=True)
    canvas = Image.new("RGBA", img.size, (200, 200, 210, 255))
    canvas.alpha_composite(body)
    px_, py_ = cfg["pivot"]
    tilted = head.rotate(25, resample=Image.BICUBIC, center=(px_, py_))
    canvas.alpha_composite(tilted)
    canvas.convert("RGB").save(prev_dir / f"tilt_{name}.jpg", quality=88)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "analyze"
    OUT.mkdir(parents=True, exist_ok=True)
    if mode == "analyze":
        for n, p in CATS.items():
            grid_preview(remove_bg(n, p), n)
    elif mode == "cut":
        for n in CATS:
            cut(n)

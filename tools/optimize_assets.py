# -*- coding: utf-8 -*-
"""把 public/assets 下的大图压缩成 WebP，兼顾「下载体积」和「解码内存」。

背景图 1920x2560 在手机上光解码就要 ~20MB，低端安卓 WebView 常常解不动，
表现就是「背景加载不出来」。这里统一缩到实际渲染需要的尺寸：

    bg.jpg            1920x2560  ->  宽 1080  WebP q75
    {cat}_body.png    ->  宽 800   WebP q90（保留 alpha）
    {cat}_head.png    ->  宽 800   WebP q90（保留 alpha，必须和 body 同比例）
    {cat}_full.png    ->  宽 360   WebP q88（结算页只显示 150px）

同时按同比例缩放 {cat}.json 里的 w / h / pivot / head_box，保证头身对齐。

用法: python tools/optimize_assets.py
"""
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "public" / "assets"
CATS = ASSETS / "cats"

BG_W, BG_Q = 1080, 75
LAYER_W, LAYER_Q = 800, 90
FULL_W, FULL_Q = 360, 88


def fit_width(img, max_w):
    """只缩不放：宽度超过 max_w 才等比缩小。"""
    if img.width <= max_w:
        return img, 1.0
    ratio = max_w / img.width
    return img.resize((max_w, round(img.height * ratio)), Image.LANCZOS), ratio


def save_webp(img, dst, quality):
    img.save(dst, "WEBP", quality=quality, method=6)
    return dst.stat().st_size


def kb(p):
    return f"{p.stat().st_size / 1024:.0f}KB"


def main():
    total_before = total_after = 0

    # ---- 背景 ----
    src = ASSETS / "bg.jpg"
    img = Image.open(src).convert("RGB")
    img, _ = fit_width(img, BG_W)
    dst = ASSETS / "bg.webp"
    save_webp(img, dst, BG_Q)
    total_before += src.stat().st_size
    total_after += dst.stat().st_size
    print(f"bg        {src.name} {kb(src)} -> {dst.name} {kb(dst)}  {img.size}")

    # ---- 猫 ----
    for js in sorted(CATS.glob("*.json")):
        name = js.stem
        meta = json.loads(js.read_text(encoding="utf-8"))
        ratio = 1.0

        for kind, max_w, q in (("body", LAYER_W, LAYER_Q),
                               ("head", LAYER_W, LAYER_Q),
                               ("full", FULL_W, FULL_Q)):
            s = CATS / f"{name}_{kind}.png"
            im = Image.open(s).convert("RGBA")
            im, r = fit_width(im, max_w)
            if kind == "body":
                ratio = r
            out = CATS / f"{name}_{kind}.webp"
            save_webp(im, out, q)
            total_before += s.stat().st_size
            total_after += out.stat().st_size
            print(f"{name}_{kind:<5} {kb(s)} -> {kb(out)}  {im.size}")

        # 头身分层必须和 body 用同一比例，meta 坐标同步缩放
        if ratio != 1.0:
            meta["w"] = round(meta["w"] * ratio)
            meta["h"] = round(meta["h"] * ratio)
            meta["pivot"] = [round(meta["pivot"][0] * ratio),
                             round(meta["pivot"][1] * ratio)]
            meta["head_box"] = [round(v * ratio) for v in meta["head_box"]]
            js.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                          encoding="utf-8")
            print(f"  {name}.json -> w={meta['w']} h={meta['h']} pivot={meta['pivot']}")

    print(f"\n图片总量 {total_before/1024/1024:.2f}MB -> {total_after/1024/1024:.2f}MB")


if __name__ == "__main__":
    main()

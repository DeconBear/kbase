"""Render KBase app icon: clean geometric K on accent blue.

Exports master PNG, 512/256/64, and multi-size ICO.
If ``kb/assets/kbase-logo-source.png`` exists, resize that instead
(use a hand-tuned / AI concept master).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).resolve().parent.parent / "assets"
SOURCE = OUT_DIR / "kbase-logo-source.png"
MASTER = 1024


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_color(
    c0: tuple[int, int, int], c1: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    t = min(1.0, max(0.0, t))
    return (
        int(_lerp(c0[0], c1[0], t)),
        int(_lerp(c0[1], c1[1], t)),
        int(_lerp(c0[2], c1[2], t)),
    )


def _mask(size: int, radius: float) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255
    )
    return m


def _apply_squircle(img: Image.Image, radius_ratio: float = 0.225) -> Image.Image:
    img = img.convert("RGBA")
    size = img.width
    if img.height != size:
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    mask = _mask(size, size * radius_ratio)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0))
    r, g, b, a = out.split()
    a = Image.composite(a, mask, mask)
    return Image.merge("RGBA", (r, g, b, a))


def _bg(size: int) -> Image.Image:
    top, mid, bot = (70, 140, 250), (53, 115, 240), (32, 78, 200)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        ty = y / (size - 1)
        base = (
            _lerp_color(top, mid, ty / 0.55)
            if ty < 0.55
            else _lerp_color(mid, bot, (ty - 0.55) / 0.45)
        )
        for x in range(size):
            tx = x / (size - 1)
            c = _lerp_color(base, bot, tx * 0.18)
            cx, cy = tx - 0.5, ty - 0.4
            vig = 1.0 - min(1.0, (cx * cx + cy * cy) * 1.35) * 0.12
            px[x, y] = (int(c[0] * vig), int(c[1] * vig), int(c[2] * vig))
    return img


def _capsule(
    draw: ImageDraw.ImageDraw,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    fill: tuple[int, int, int, int],
) -> None:
    """Axis-aligned capsule (rounded rectangle with full round ends)."""
    w, h = abs(x1 - x0), abs(y1 - y0)
    r = min(w, h) * 0.5
    draw.rounded_rectangle((x0, y0, x1, y1), radius=r, fill=fill)


def _draw_k(layer: Image.Image, size: int) -> None:
    s = float(size)
    d = ImageDraw.Draw(layer, "RGBA")
    white = (255, 255, 255, 255)

    left = s * 0.28
    stem_w = s * 0.16
    top = s * 0.20
    bot = s * 0.80
    join_x = left + stem_w * 0.92
    mid_y = (top + bot) * 0.5
    arm_t = s * 0.145  # arm thickness

    # Soft shadow
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow, "RGBA")
    off = s * 0.016
    sd.rounded_rectangle(
        (left + off, top + off, left + stem_w + off, bot + off),
        radius=stem_w * 0.5,
        fill=(12, 36, 90, 45),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=s * 0.022))
    layer.alpha_composite(shadow)

    # Stem — full capsule
    _capsule(d, left, top, left + stem_w, bot, white)

    def _arm(x_in: float, y_in: float, x_out: float, y_out: float) -> None:
        dx, dy = x_out - x_in, y_out - y_in
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        nx, ny = -dy / length * (arm_t * 0.5), dx / length * (arm_t * 0.5)
        poly = [
            (x_in + nx, y_in + ny),
            (x_out + nx, y_out + ny),
            (x_out - nx, y_out - ny),
            (x_in - nx, y_in - ny),
        ]
        d.polygon(poly, fill=white)
        r = arm_t * 0.5
        d.ellipse((x_out - r, y_out - r, x_out + r, y_out + r), fill=white)
        d.ellipse((x_in - r * 0.85, y_in - r * 0.85, x_in + r * 0.85, y_in + r * 0.85), fill=white)

    _arm(join_x, mid_y - s * 0.01, s * 0.74, top + s * 0.06)
    _arm(join_x, mid_y + s * 0.01, s * 0.74, bot - s * 0.06)

    # Gold diamond in the K crotch
    sx, sy = join_x + s * 0.055, mid_y
    r = s * 0.032
    d.polygon(
        [(sx, sy - r), (sx + r * 0.78, sy), (sx, sy + r), (sx - r * 0.78, sy)],
        fill=(255, 208, 74, 255),
    )
    d.polygon(
        [
            (sx, sy - r * 0.38),
            (sx + r * 0.28, sy),
            (sx, sy + r * 0.38),
            (sx - r * 0.28, sy),
        ],
        fill=(255, 255, 255, 200),
    )


def render_procedural(size: int = MASTER) -> Image.Image:
    bg = _bg(size).convert("RGBA")
    sheen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen, "RGBA")
    for i in range(size // 2):
        a = int(18 * (1 - i / (size / 2)))
        sd.line([(0, i), (size, i)], fill=(255, 255, 255, a))
    bg.alpha_composite(sheen)

    mark = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    _draw_k(mark, size)
    soft = mark.filter(ImageFilter.GaussianBlur(radius=max(1, size // 800)))
    bg.alpha_composite(soft)
    bg.alpha_composite(mark)
    return _apply_squircle(bg)


def render_master(size: int = MASTER) -> Image.Image:
    if SOURCE.is_file():
        src = Image.open(SOURCE).convert("RGBA")
        src = src.resize((size, size), Image.Resampling.LANCZOS)
        return _apply_squircle(src)
    return render_procedural(size)


def export_all() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    master = render_master(MASTER)
    master.save(OUT_DIR / "kbase-logo.png", "PNG")
    print("wrote", OUT_DIR / "kbase-logo.png", "(source)" if SOURCE.is_file() else "(procedural)")
    for side in (512, 256, 64):
        path = OUT_DIR / f"kbase-logo-{side}.png"
        master.resize((side, side), Image.Resampling.LANCZOS).save(
            path, "PNG", optimize=True
        )
        print("wrote", path)
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icos = [master.resize(sz, Image.Resampling.LANCZOS) for sz in ico_sizes]
    ico_path = OUT_DIR / "kbase-logo.ico"
    icos[-1].save(ico_path, format="ICO", sizes=ico_sizes, append_images=icos[:-1])
    print("wrote", ico_path)


if __name__ == "__main__":
    export_all()

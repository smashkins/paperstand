#!/usr/bin/env python3
"""Rasterise the application icon into the PNGs a browser and a phone want.

`frontend/static/favicon.svg` is the source of truth and the only thing to edit;
this redraws the same shapes with Pillow, because the manifest, the iOS home
screen and `/favicon.ico` all want bitmaps and nothing in the toolchain renders
SVG. Run it after changing the mark:

    uv run --project backend python scripts/make_icons.py

The maskable variant is the same mark inside the safe zone a launcher may crop
to a circle: the tile bleeds to the edges and the page shrinks to 60% so that
nothing meaningful lives outside the middle 80%.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

#: The palette of the light theme, from `frontend/src/app.css`.
ACCENT = (200, 16, 46, 255)
PAPER = (248, 245, 239, 255)
RULE = (219, 122, 137, 255)  # ACCENT at 45% over PAPER, flattened.

STATIC = Path(__file__).resolve().parents[1] / "frontend" / "static"

#: Every icon that ends up in `frontend/static/`, with the tile's corner radius
#: as a fraction of its side. A maskable icon is square to the edge (`0.0`); a
#: home-screen icon on iOS is masked by the system, so it is square too.
ICONS: tuple[tuple[str, int, float, float], ...] = (
    # name, size, radius fraction, page scale
    ("icon-192.png", 192, 0.22, 1.0),
    ("icon-512.png", 512, 0.22, 1.0),
    ("icon-512-maskable.png", 512, 0.0, 0.6),
    ("apple-touch-icon.png", 180, 0.0, 1.0),
)


def draw_icon(size: int, radius_fraction: float, page_scale: float) -> Image.Image:
    """The mark of `favicon.svg`, at `size` pixels, as an opaque RGBA image."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = size / 32

    radius = size * radius_fraction
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=ACCENT)

    # The page and its three rules, in the 32-unit grid `favicon.svg` uses,
    # scaled about the centre so that the maskable tile keeps its safe zone.
    def box(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        left = (16 + (x - 16) * page_scale) * unit
        top = (16 + (y - 16) * page_scale) * unit
        return (left, top, left + w * page_scale * unit, top + h * page_scale * unit)

    draw.rounded_rectangle(box(7, 6, 18, 20), radius=1.5 * unit * page_scale, fill=PAPER)
    draw.rectangle(box(10, 9, 12, 3.5), fill=ACCENT)
    draw.rectangle(box(10, 15.5, 12, 2), fill=RULE)
    draw.rectangle(box(10, 19.5, 8, 2), fill=RULE)
    return image


def main() -> None:
    """Write every icon, and the multi-resolution `favicon.ico` beside them."""
    for name, size, radius_fraction, page_scale in ICONS:
        draw_icon(size, radius_fraction, page_scale).save(STATIC / name)
        print(f"wrote {name}")

    # `/favicon.ico` is what a browser asks for before it has seen any markup,
    # and what a feed reader or a bookmark bar falls back to.
    master = draw_icon(256, 0.22, 1.0)
    master.save(STATIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("wrote favicon.ico")


if __name__ == "__main__":
    main()

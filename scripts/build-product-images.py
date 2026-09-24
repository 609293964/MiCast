"""Resize product screenshots to the fnOS store promo size (1780x1004).

Images are cover-scaled (no distortion) and center-cropped to the target
aspect ratio, so the extra/missing pixels are trimmed symmetrically.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

DEFAULT_SIZE = (1780, 1004)


def fit(path: Path, size: tuple[int, int]) -> None:
    with Image.open(path) as image:
        image = image.convert("RGB")
        scale = max(size[0] / image.width, size[1] / image.height)
        resized = image.resize(
            (round(image.width * scale), round(image.height * scale)),
            Image.LANCZOS,
        )
        left = (resized.width - size[0]) // 2
        top = (resized.height - size[1]) // 2
        cropped = resized.crop((left, top, left + size[0], top + size[1]))
        cropped.save(path, format="PNG", optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory",
        nargs="?",
        default="docs/screenshots",
        help="directory holding the product screenshots",
    )
    parser.add_argument("--width", type=int, default=DEFAULT_SIZE[0])
    parser.add_argument("--height", type=int, default=DEFAULT_SIZE[1])
    args = parser.parse_args()

    size = (args.width, args.height)
    directory = Path(args.directory)
    paths = sorted(directory.glob("*.png"))
    if not paths:
        print(f"no PNG found in {directory}")
        return 1

    for path in paths:
        with Image.open(path) as image:
            before = image.size
        fit(path, size)
        print(f"{path} {before[0]}x{before[1]} -> {size[0]}x{size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

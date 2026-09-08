# MiCast platform icons

Regenerate with `.venv\Scripts\python.exe assets\export_icons.py`.
This also syncs Web icons into `web/public/icons` and rebuilds the ZIP archive.

- Windows: rounded transparent tile, independently rendered frames, 32-bit BGRA DIB plus explicit 1-bit AND mask. Area averaging avoids transparent-edge ringing. Do not replace this with a square tile to conceal a mask defect.
- Windows 16–48 px frames use binary outer alpha for shell image-list compatibility. Interior speaker edges retain antialiasing; larger frames retain smooth outer alpha.
- iOS: `apple-touch-icon.png` is an opaque 180×180 RGB image with a full terracotta background. iOS supplies its own corner mask.
- fnOS: 64×64 and 256×256 sRGB PNG files with transparent corners; the tile occupies 97% of the canvas. The speaker group is about 12% larger than the previous cropped fnOS assets, with a stronger rear outline (3.05 design units; 3.25 at 64 px). The export script synchronizes these files to `web/public/icons/fnos-64.png` and `fnos-256.png`, used by both fnOS packaging scripts.
- Browser tabs: `favicon.svg` / `favicon.ico` use a full-width rounded tile, 28% larger speaker group and stronger rear outline. The in-page `micast.svg` retains the approved application proportions.

Use stable names without revision suffixes. `fnos`, `windows`, `tray`, and `web` contain purpose-specific exports of the one approved design; numerical size suffixes indicate pixel dimensions. The current package is `assets/micast-icons.zip`. Native verification previews belong in `.run`, not in the distributable assets.

Validate on Windows with `.venv\Scripts\python.exe assets\verify_icons.py --exe` after rebuilding. The check compares generated frames with the EXE resources, checks alpha/AND-mask consistency, renders icons using native DrawIconEx, and checks the touch icon's size and opaque corners.

After deploying updated Web resources, reload the page and recreate an existing iOS home-screen shortcut. Existing installed shortcuts may retain their previous icon.

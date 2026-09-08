"""DAAP/DMAP metadata parsing (phone → receiver track info via SET_PARAMETER).

Tag semantics follow shairport-sync-metadata-reader: minm=title, asar=artist,
asal=album. Nested container tags are flattened recursively.
"""

import re

_DMAP_CONTAINER_TAGS = ("mdcl", "mlit", "msrv", "mlcl")


def parse_dmap(data: bytes) -> dict[str, list[bytes]]:
    """Parse DAAP TLV into {4-char tag: [payload, ...]}; containers flattened."""
    result: dict[str, list[bytes]] = {}
    _walk(data, result)
    return result


def _walk(data: bytes, out: dict[str, list[bytes]]) -> None:
    pos, n = 0, len(data)
    while pos + 8 <= n:
        tag = data[pos : pos + 4].decode("ascii", errors="replace")
        length = int.from_bytes(data[pos + 4 : pos + 8], "big")
        pos += 8
        if pos + length > n:
            break  # truncated; drop the rest
        payload = data[pos : pos + length]
        pos += length
        if tag in _DMAP_CONTAINER_TAGS:
            _walk(payload, out)
        else:
            out.setdefault(tag, []).append(payload)


def _strip_artist_suffix(title: str, artist: str) -> str:
    """QQ音乐 puts the artist at the end of minm ("青花瓷 - 周杰伦"),
    Apple Music too ("连名带姓 · 季末"); strip it when the suffix and the
    artist field corroborate each other."""
    for sep in (" - ", " · ", "-", "·"):
        idx = title.find(sep)
        if idx <= 0:
            continue
        prefix, suffix = title[:idx].strip(), title[idx + len(sep):].strip()
        if not prefix or not suffix:
            continue
        if artist:
            parts = re.split(r"--+| — | · |—|·", artist)
            names = {artist, *(p.strip() for p in parts if p.strip())}
            derived = _derive_title_from_artist(artist)
            if derived:
                names.add(derived)
            if any(n and (n in suffix or suffix in n) for n in names):
                return prefix
        else:
            return prefix
    return title


def _strip_artist_prefix(title: str, artist: str) -> str:
    """QQ音乐 prefix form: "周杰伦--告白气球" — strip when the prefix
    corroborates the artist field (which may carry an album tail)."""
    artist = artist.strip()
    if not artist:
        return title
    for sep in ("--", " - ", "—"):
        idx = title.find(sep)
        if idx <= 0:
            continue
        prefix, rest = title[:idx].strip(), title[idx + len(sep):].strip()
        if not prefix or not rest:
            continue
        if prefix == artist or artist.startswith(prefix):
            return rest
    return title


def _derive_title_from_artist(artist: str) -> str:
    """Some senders never send the real title in minm (only scrolling lyrics
    and credit lines); it hides inside asar: "挚友 · Eric周兴哲",
    "搁浅 — 周杰伦" (title first), QQ音乐 "周杰伦--告白气球" (title last)."""
    for sep in (" · ", " — ", "·", "—"):
        if sep in artist:
            parts = [p.strip() for p in artist.split(sep)]
            if len(parts) == 2 and all(parts):
                return parts[0]
    if "--" in artist:
        prefix, _, suffix = artist.partition("--")
        if prefix.strip() and suffix.strip():
            return suffix.strip()
    return ""


def track_meta(body: bytes) -> dict[str, str]:
    """Extract {title, artist, album, derived} from a SET_PARAMETER dmap body."""
    tags = parse_dmap(body)

    def first(tag: str) -> str:
        values = tags.get(tag) or []
        return values[0].decode("utf-8", errors="replace").strip() if values else ""

    raw_title = first("minm")
    if not raw_title:
        return {}
    artist = first("asar")
    title = _strip_artist_prefix(_strip_artist_suffix(raw_title, artist), artist)
    return {
        "title": title,
        "artist": artist,
        "album": first("asal"),
        "derived": _derive_title_from_artist(artist),
    }

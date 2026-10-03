#!/usr/bin/env python3
"""Stamp X-Ray v3 Vorbis comments onto .ogg files and rewrite OGG page CRCs.

Audacity drops the binary first comment. Engine then uses ctor max_dist 300.
This puts the pack blob back without re-encoding audio.

  py -3 stamp_ogg_comments.py
  py -3 stamp_ogg_comments.py path/to/file.ogg --check

Dir + defaults live in stamp_ogg_comments.ini next to this script. CLI flags override.
"""

from __future__ import annotations

import argparse
import configparser
import struct
import sys
from pathlib import Path

INI_NAME = "stamp_ogg_comments.ini"


def ogg_crc_table() -> list[int]:
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            if r & 0x80000000:
                r = (r << 1) ^ 0x04C11DB7
            else:
                r <<= 1
            r &= 0xFFFFFFFF
        table.append(r)
    return table


CRC_TAB = ogg_crc_table()


def crc32_ogg(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ CRC_TAB[((crc >> 24) & 0xFF) ^ b]
    return crc


def iter_pages(data: bytes) -> list[bytes]:
    pages = []
    i = 0
    while i + 27 <= len(data):
        if data[i : i + 4] != b"OggS":
            raise ValueError(f"bad OGG magic at {i}")
        nseg = data[i + 26]
        body = sum(data[i + 27 : i + 27 + nseg])
        end = i + 27 + nseg + body
        if end > len(data):
            raise ValueError("truncated OGG page")
        pages.append(data[i:end])
        i = end
    if i != len(data):
        raise ValueError(f"trailing {len(data) - i} bytes")
    return pages


def page_crc_ok(page: bytes) -> bool:
    stored = int.from_bytes(page[22:26], "little")
    buf = bytearray(page)
    buf[22:26] = b"\x00\x00\x00\x00"
    return stored == crc32_ogg(bytes(buf))


def packets_from_pages(pages: list[bytes]) -> list[tuple[bytes, int]]:
    """(packet, granule of the page that completed it)."""
    out: list[tuple[bytes, int]] = []
    buf = b""
    for page in pages:
        granule = int.from_bytes(page[6:14], "little")
        nseg = page[26]
        segs = page[27 : 27 + nseg]
        body = page[27 + nseg :]
        off = 0
        for s in segs:
            buf += body[off : off + s]
            off += s
            if s < 255:
                out.append((buf, granule))
                buf = b""
    if buf:
        raise ValueError("truncated Vorbis packet")
    return out


def lace_packet(pkt: bytes) -> list[int]:
    if not pkt:
        return [0]
    segs: list[int] = []
    off = 0
    while off < len(pkt):
        n = min(255, len(pkt) - off)
        segs.append(n)
        off += n
    if len(pkt) % 255 == 0:
        segs.append(0)
    return segs


def make_page_packets(serial: int, seq: int, granule: int, flags: int, packets: list[bytes]) -> bytes:
    segs: list[int] = []
    for pkt in packets:
        segs.extend(lace_packet(pkt))
    if not (1 <= len(segs) <= 255):
        raise ValueError(f"bad segment count {len(segs)}")
    body = b"".join(packets)
    header = bytearray(27 + len(segs))
    header[0:4] = b"OggS"
    header[4] = 0
    header[5] = flags
    header[6:14] = granule.to_bytes(8, "little")
    header[14:18] = serial.to_bytes(4, "little")
    header[18:22] = seq.to_bytes(4, "little")
    header[22:26] = b"\x00\x00\x00\x00"
    header[26] = len(segs)
    header[27:] = bytes(segs)
    page = bytes(header) + body
    crc = crc32_ogg(page)
    return page[:22] + crc.to_bytes(4, "little") + page[26:]


def pack_audio_pages(serial: int, seq0: int, packets: list[bytes], last_gran: int) -> tuple[list[bytes], int]:
    pages: list[bytes] = []
    seq = seq0
    batch: list[bytes] = []
    segs = 0
    for i, pkt in enumerate(packets):
        extra = len(lace_packet(pkt))
        if batch and segs + extra > 255:
            pages.append(make_page_packets(serial, seq, 0, 0, batch))
            seq += 1
            batch = []
            segs = 0
        batch.append(pkt)
        segs += extra
    if batch:
        pages.append(make_page_packets(serial, seq, last_gran, 0x04, batch))
        seq += 1
    return pages, seq


def parse_comment_packet(pkt: bytes) -> tuple[bytes, list[bytes]]:
    if not (pkt[0] == 3 and pkt[1:7] == b"vorbis"):
        raise ValueError("not a Vorbis comment packet")
    i = 7
    vlen = struct.unpack_from("<I", pkt, i)[0]
    i += 4
    vendor = pkt[i : i + vlen]
    i += vlen
    n = struct.unpack_from("<I", pkt, i)[0]
    i += 4
    comments = []
    for _ in range(n):
        clen = struct.unpack_from("<I", pkt, i)[0]
        i += 4
        comments.append(pkt[i : i + clen])
        i += clen
    return vendor, comments


def build_comment_packet(vendor: bytes, comments: list[bytes]) -> bytes:
    parts = [b"\x03vorbis", struct.pack("<I", len(vendor)), vendor, struct.pack("<I", len(comments))]
    for c in comments:
        parts.append(struct.pack("<I", len(c)))
        parts.append(c)
    parts.append(b"\x01")  # framing bit
    return b"".join(parts)


def xray_blob(min_dist: float, max_dist: float, vol: float, game_type: int, ai_dist: float) -> bytes:
    return struct.pack("<IfffIf", 3, min_dist, max_dist, vol, game_type, ai_dist)


def parse_xray(c0: bytes) -> dict | None:
    if len(c0) < 4:
        return None
    vers = struct.unpack_from("<I", c0, 0)[0]
    if vers == 1 and len(c0) >= 16:
        return {
            "vers": 1,
            "min_dist": struct.unpack_from("<f", c0, 4)[0],
            "max_dist": struct.unpack_from("<f", c0, 8)[0],
            "game_type": struct.unpack_from("<I", c0, 12)[0],
        }
    if vers == 2 and len(c0) >= 20:
        return {
            "vers": 2,
            "min_dist": struct.unpack_from("<f", c0, 4)[0],
            "max_dist": struct.unpack_from("<f", c0, 8)[0],
            "base_volume": struct.unpack_from("<f", c0, 12)[0],
            "game_type": struct.unpack_from("<I", c0, 16)[0],
        }
    if vers == 3 and len(c0) >= 24:
        return {
            "vers": 3,
            "min_dist": struct.unpack_from("<f", c0, 4)[0],
            "max_dist": struct.unpack_from("<f", c0, 8)[0],
            "base_volume": struct.unpack_from("<f", c0, 12)[0],
            "game_type": struct.unpack_from("<I", c0, 16)[0],
            "max_ai_dist": struct.unpack_from("<f", c0, 20)[0],
        }
    return None


def keep_meta(comments: list[bytes]) -> list[bytes]:
    kept = []
    for c in comments:
        if parse_xray(c) is not None:
            continue
        if b"=" not in c:
            continue
        kept.append(c)
    return kept


def ident_info(pkt: bytes) -> tuple[int, int]:
    if not (pkt[0] == 1 and pkt[1:7] == b"vorbis"):
        raise ValueError("not a Vorbis ident packet")
    ch = pkt[11]
    rate = struct.unpack_from("<I", pkt, 12)[0]
    return rate, ch


def stamp_file(
    path: Path,
    *,
    min_dist: float,
    max_dist: float,
    vol: float,
    ai_dist: float,
    game_type: int,
    dry: bool,
) -> str:
    data = path.read_bytes()
    pages = iter_pages(data)
    serial = int.from_bytes(pages[0][14:18], "little")
    packets = packets_from_pages(pages)
    if len(packets) < 3:
        raise ValueError("expected ident + comment + setup")
    ident, _ = packets[0]
    comment, _ = packets[1]
    rate, ch = ident_info(ident)
    vendor, comments = parse_comment_packet(comment)
    old = parse_xray(comments[0]) if comments else None
    blob = xray_blob(min_dist, max_dist, vol, game_type, ai_dist)
    new_comments = [blob] + keep_meta(comments)
    new_comment = build_comment_packet(vendor, new_comments)

    # Vorbis I + X-Ray: page0 ident (BOS), page1 comment+setup, then audio. Last page EOS.
    # One-packet-per-page made ov_pcm_total 0 on the engine decoder.
    setup = packets[2][0]
    audio = [pkt for pkt, _ in packets[3:]]
    last_gran = packets[-1][1] if len(packets) > 3 else 0
    out_pages = [make_page_packets(serial, 0, 0, 0x02, [ident])]
    out_pages.append(make_page_packets(serial, 1, 0, 0, [new_comment, setup]))
    more, _ = pack_audio_pages(serial, 2, audio, last_gran)
    out_pages.extend(more)
    new = b"".join(out_pages)
    if not dry:
        path.write_bytes(new)

    crc_bad = sum(1 for p in out_pages if not page_crc_ok(p))
    if crc_bad:
        raise RuntimeError(f"{path.name}: wrote {crc_bad} bad CRC pages")
    return (
        f"{path.name}: {rate}Hz ch={ch} "
        f"min={min_dist:g} max={max_dist:g} vol={vol:g} ai={ai_dist:g} "
        f"was={'v'+str(old['vers']) if old else 'none'}"
    )


def inspect_file(path: Path) -> str:
    data = path.read_bytes()
    pages = iter_pages(data)
    bad = [i for i, p in enumerate(pages) if not page_crc_ok(p)]
    packets = packets_from_pages(pages)
    rate, ch = ident_info(packets[0][0])
    _, comments = parse_comment_packet(packets[1][0])
    xr = parse_xray(comments[0]) if comments else None
    if xr:
        blob = (
            f"v{xr['vers']} min={xr['min_dist']:g} max={xr['max_dist']:g}"
            + (f" vol={xr['base_volume']:g}" if "base_volume" in xr else "")
            + (f" ai={xr['max_ai_dist']:g}" if "max_ai_dist" in xr else "")
        )
    else:
        blob = "NO xray blob"
    return f"{path.name}: {rate}Hz ch={ch} crc_bad={bad} {blob}"


def resolve_path(p: Path, here: Path) -> Path:
    if p.is_absolute():
        return p
    if p.exists():
        return p
    cand = here / p
    if cand.exists():
        return cand
    return p


def load_ini(here: Path) -> dict:
    path = here / INI_NAME
    if not path.is_file():
        raise SystemExit(f"missing {path}")
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(path, encoding="utf-8")
    s = cfg["stamp"]
    max_dist = s.getfloat("max_dist")
    ai_raw = s.get("max_ai_dist", fallback="").strip()
    return {
        "dir": s.get("dir", fallback="").strip(),
        "min_dist": s.getfloat("min_dist"),
        "max_dist": max_dist,
        "vol": s.getfloat("base_volume"),
        "ai_dist": float(ai_raw) if ai_raw else max_dist,
        "game_type": int(s.get("game_type"), 0),
    }


def collect(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out.extend(sorted(p.rglob("*.ogg")))
        elif p.suffix.lower() == ".ogg":
            out.append(p)
        else:
            raise SystemExit(f"not an ogg or dir: {p}")
    return out


def main() -> int:
    here = Path(__file__).resolve().parent
    ini = load_ini(here)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", type=Path, help="ogg files or dirs (default: ini dir)")
    ap.add_argument("--min", dest="min_dist", type=float, default=None)
    ap.add_argument("--max", dest="max_dist", type=float, default=None)
    ap.add_argument("--vol", dest="vol", type=float, default=None)
    ap.add_argument("--ai", dest="ai_dist", type=float, default=None)
    ap.add_argument("--game-type", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--check", action="store_true", help="print current comments, do not write")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    min_dist = ini["min_dist"] if args.min_dist is None else args.min_dist
    max_dist = ini["max_dist"] if args.max_dist is None else args.max_dist
    vol = ini["vol"] if args.vol is None else args.vol
    ai = ini["ai_dist"] if args.ai_dist is None else args.ai_dist
    game_type = ini["game_type"] if args.game_type is None else args.game_type
    raw_paths = args.paths or ([Path(ini["dir"])] if ini["dir"] else [])
    if not raw_paths:
        raise SystemExit(f"set dir= in {INI_NAME} or pass a path")
    files = collect([resolve_path(p, here) for p in raw_paths])
    if not files:
        print("no ogg files", file=sys.stderr)
        return 1
    for f in files:
        if args.check:
            print(inspect_file(f))
        else:
            print(stamp_file(
                f,
                min_dist=min_dist,
                max_dist=max_dist,
                vol=vol,
                ai_dist=ai,
                game_type=game_type,
                dry=args.dry_run,
            ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

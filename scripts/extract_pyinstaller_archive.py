from __future__ import annotations

import argparse
import marshal
import struct
import zlib
from pathlib import Path


MAGIC = b"MEI\014\013\012\013\016"
COOKIE_SIZE = 88


def extract(source: Path, output_dir: Path) -> dict[str, int]:
    data = source.read_bytes()
    magic_pos = data.rfind(MAGIC)
    if magic_pos < 0:
        raise ValueError(f"PyInstaller cookie not found: {source}")

    cookie = data[magic_pos:magic_pos + COOKIE_SIZE]
    if len(cookie) < COOKIE_SIZE:
        raise ValueError("truncated PyInstaller cookie")

    _magic, package_len, toc_offset, toc_len, pyver, pylib = struct.unpack("!8sIIII64s", cookie)
    package_start = magic_pos + COOKIE_SIZE - package_len
    toc_start = package_start + toc_offset
    toc_end = toc_start + toc_len
    if package_start < 0 or toc_start < 0 or toc_end > len(data):
        raise ValueError("invalid PyInstaller archive offsets")

    output_dir.mkdir(parents=True, exist_ok=True)
    toc_rows = []
    extracted = 0
    compressed = 0
    pos = toc_start
    while pos < toc_end:
        header = data[pos:pos + 18]
        if len(header) < 18:
            break
        entry_size, entry_pos, compressed_size, uncompressed_size, compress_flag, type_code = struct.unpack(
            "!IIIIBc",
            header,
        )
        name_bytes = data[pos + 18:pos + entry_size]
        name = name_bytes.split(b"\0", 1)[0].decode("utf-8", errors="replace")
        pos += entry_size

        payload = data[package_start + entry_pos:package_start + entry_pos + compressed_size]
        if compress_flag:
            payload = zlib.decompress(payload)
            compressed += 1
        if uncompressed_size and len(payload) != uncompressed_size:
            print(f"[warn] size mismatch: {name} got={len(payload)} expected={uncompressed_size}")

        safe_name = safe_output_name(name, type_code.decode("ascii", errors="replace"))
        out_path = output_dir / safe_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(payload)
        extracted += 1
        toc_rows.append((type_code.decode("ascii", errors="replace"), compress_flag, name, safe_name, len(payload)))

    (output_dir / "_toc.tsv").write_text(
        "type\tcompressed\tname\toutput\tsize\n"
        + "\n".join(f"{t}\t{c}\t{name}\t{out}\t{size}" for t, c, name, out, size in toc_rows)
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "_archive_info.txt").write_text(
        "\n".join([
            f"source={source}",
            f"package_start={package_start}",
            f"package_len={package_len}",
            f"toc_offset={toc_offset}",
            f"toc_len={toc_len}",
            f"pyver={pyver}",
            f"pylib={pylib.split(bytes([0]), 1)[0].decode('utf-8', errors='replace')}",
            f"entries={extracted}",
            f"compressed_entries={compressed}",
        ])
        + "\n",
        encoding="utf-8",
    )
    return {"entries": extracted, "compressed_entries": compressed}


def safe_output_name(name: str, type_code: str) -> Path:
    cleaned = name.replace("\\", "/").strip("/")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = f"entry_{type_code}"
    parts = [part for part in cleaned.split("/") if part not in {"", ".", ".."}]
    path = Path(*parts)
    if type_code in {"s", "m", "M"} and path.suffix not in {".pyc", ".py"}:
        path = path.with_suffix(".pyc")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract a PyInstaller CArchive without running it.")
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    result = extract(args.source, args.output_dir)
    print(result)


if __name__ == "__main__":
    main()

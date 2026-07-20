#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from xml.sax.saxutils import quoteattr


PROJECT_DIR = Path(__file__).resolve().parents[1]
SOURCE_PATH = PROJECT_DIR / "domains/flood/data/sources/osm_roads_shanhu.json"
ROUTING_DIR = PROJECT_DIR / "local/routing"
OSM_PATH = ROUTING_DIR / "shanhu-roads.osm.xml"
JAR_PATH = ROUTING_DIR / "graphhopper-web-11.0.jar"
GRAPH_CACHE_PATH = ROUTING_DIR / "graph-cache"
CONFIG_PATH = PROJECT_DIR / "domains/flood/routing/graphhopper.yml"
JAR_URL = "https://github.com/graphhopper/graphhopper/releases/download/11.0/graphhopper-web-11.0.jar"
JAR_SHA256 = "b59c024afe172ec6ec85b6327006c3138ec58c7d0bcd26253d0e42853f613def"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare and run the local GraphHopper routing service.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="Build OSM XML and download GraphHopper.")
    prepare.add_argument("--force-osm", action="store_true")
    prepare.add_argument("--force-jar", action="store_true")
    subparsers.add_parser("serve", help="Run GraphHopper in the foreground.")
    subparsers.add_parser("status", help="Check the GraphHopper HTTP service.")
    args = parser.parse_args()

    if args.command == "prepare":
        ROUTING_DIR.mkdir(parents=True, exist_ok=True)
        if args.force_osm or not OSM_PATH.exists():
            stats = build_osm_xml(SOURCE_PATH, OSM_PATH)
            print(f"OSM routing network: {stats['ways']} ways, {stats['nodes']} nodes -> {OSM_PATH}")
        else:
            print(f"OSM routing network exists: {OSM_PATH}")
        if args.force_jar or not valid_jar(JAR_PATH):
            download_jar(JAR_PATH)
        print(f"GraphHopper JAR: {JAR_PATH}")
        return 0

    if args.command == "serve":
        if not OSM_PATH.exists() or not valid_jar(JAR_PATH):
            print("Run `python scripts/graphhopper.py prepare` first.", file=sys.stderr)
            return 2
        command = [
            "java",
            f"-Ddw.graphhopper.datareader.file={OSM_PATH}",
            f"-Ddw.graphhopper.graph.location={GRAPH_CACHE_PATH}",
            "-Xms512m",
            "-Xmx2g",
            "-jar",
            str(JAR_PATH),
            "server",
            str(CONFIG_PATH),
        ]
        return subprocess.run(command, cwd=PROJECT_DIR, check=False).returncode

    try:
        with urlopen("http://127.0.0.1:8989/info", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (OSError, URLError, json.JSONDecodeError) as exc:
        print(f"GraphHopper is unavailable: {exc}", file=sys.stderr)
        return 1


def build_osm_xml(source: Path, target: Path) -> dict[str, int]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    ways = [item for item in payload.get("elements", []) if item.get("type") == "way" and item.get("geometry")]
    node_ids: dict[tuple[float, float], int] = {}
    for way in ways:
        for point in way["geometry"]:
            key = coordinate_key(point["lon"], point["lat"])
            if key not in node_ids:
                node_ids[key] = len(node_ids) + 1

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as output:
        output.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        output.write('<osm version="0.6" generator="flood-graphhopper-export">\n')
        for (lon, lat), node_id in node_ids.items():
            output.write(
                f'  <node id="{node_id}" lat="{lat:.7f}" lon="{lon:.7f}" version="1" visible="true"/>\n'
            )
        for way in ways:
            output.write(f'  <way id="{int(way["id"])}" version="1" visible="true">\n')
            for point in way["geometry"]:
                output.write(f'    <nd ref="{node_ids[coordinate_key(point["lon"], point["lat"])]}"/>\n')
            for key, value in sorted((way.get("tags") or {}).items()):
                output.write(f"    <tag k={quoteattr(str(key))} v={quoteattr(str(value))}/>\n")
            output.write("  </way>\n")
        output.write("</osm>\n")
    return {"ways": len(ways), "nodes": len(node_ids)}


def coordinate_key(lon: float, lat: float) -> tuple[float, float]:
    return round(float(lon), 7), round(float(lat), 7)


def valid_jar(path: Path) -> bool:
    return path.exists() and file_sha256(path) == JAR_SHA256


def download_jar(target: Path) -> None:
    temp = target.with_suffix(".jar.download")
    if temp.exists():
        temp.unlink()
    print(f"Downloading {JAR_URL}")
    try:
        with urlopen(JAR_URL, timeout=60) as response, temp.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except Exception:
        if temp.exists():
            temp.unlink()
        raise
    digest = file_sha256(temp)
    if digest != JAR_SHA256:
        temp.unlink()
        raise RuntimeError(f"GraphHopper JAR checksum mismatch: {digest}")
    temp.replace(target)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())

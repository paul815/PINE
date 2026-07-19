#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEST = REPO_ROOT / "backend" / "app" / "static" / "fonts"
GITHUB = "https://github.com/google/fonts/raw/main/ofl"

SOURCES = {
    "inter": f"{GITHUB}/inter/Inter%5Bopsz%2Cwght%5D.ttf",
    "roboto": f"{GITHUB}/roboto/Roboto%5Bwdth%2Cwght%5D.ttf",
    "opensans": f"{GITHUB}/opensans/OpenSans%5Bwdth%2Cwght%5D.ttf",
    "sourcesans3": f"{GITHUB}/sourcesans3/SourceSans3%5Bwght%5D.ttf",
    "jetbrainsmono": f"{GITHUB}/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
}

UNICODES = (
    "U+0020-007F,U+00A0-00FF,U+0100-017F,U+0180-024F,U+0400-04FF,U+0500-052F,"
    "U+2000-206F,U+20AC,U+2116,U+2122,U+2212,U+0300-0301,U+0304,U+0308,U+0323,U+0329,"
    "U+FEFF,U+FFFD"
)

JOBS = [
    ("Inter-Regular.woff2", "inter", ["wght=400", "opsz=14"]),
    ("Inter-Medium.woff2", "inter", ["wght=500", "opsz=14"]),
    ("Inter-SemiBold.woff2", "inter", ["wght=600", "opsz=14"]),
    ("Inter-Bold.woff2", "inter", ["wght=700", "opsz=14"]),
    ("Roboto-Regular.woff2", "roboto", ["wght=400", "wdth=100"]),
    ("Roboto-Medium.woff2", "roboto", ["wght=500", "wdth=100"]),
    ("Roboto-Bold.woff2", "roboto", ["wght=700", "wdth=100"]),
    ("OpenSans-Regular.woff2", "opensans", ["wght=400", "wdth=100"]),
    ("OpenSans-Medium.woff2", "opensans", ["wght=500", "wdth=100"]),
    ("OpenSans-SemiBold.woff2", "opensans", ["wght=600", "wdth=100"]),
    ("OpenSans-Bold.woff2", "opensans", ["wght=700", "wdth=100"]),
    ("SourceSans3-Regular.woff2", "sourcesans3", ["wght=400"]),
    ("SourceSans3-Medium.woff2", "sourcesans3", ["wght=500"]),
    ("SourceSans3-SemiBold.woff2", "sourcesans3", ["wght=600"]),
    ("SourceSans3-Bold.woff2", "sourcesans3", ["wght=700"]),
    ("JetBrainsMono-Regular.woff2", "jetbrainsmono", ["wght=400"]),
    ("JetBrainsMono-Medium.woff2", "jetbrainsmono", ["wght=500"]),
]


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (build_cyrillic_fonts)"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if not DEST.is_dir():
        print("DEST missing:", DEST, file=sys.stderr)
        return 1
    cache: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="port_fonts_") as tmp:
        tmp_path = Path(tmp)
        for out_name, src_key, inst_args in JOBS:
            if args.dry_run:
                print(out_name, "<-", src_key, inst_args)
                continue
            if src_key not in cache:
                ttf = tmp_path / f"{src_key}_var.ttf"
                print("Downloading", src_key)
                _download(SOURCES[src_key], ttf)
                cache[src_key] = ttf
            var_ttf = cache[src_key]
            inst_ttf = tmp_path / f"inst_{out_name.replace('.woff2', '.ttf')}"
            woff2_out = DEST / out_name
            _run(
                [sys.executable, "-m", "fontTools.varLib.instancer", str(var_ttf), *inst_args, "-o", str(inst_ttf)]
            )
            _run(
                [
                    sys.executable,
                    "-m",
                    "fontTools.subset",
                    str(inst_ttf),
                    f"--output-file={woff2_out}",
                    "--flavor=woff2",
                    "--with-zopfli",
                    f"--unicodes={UNICODES}",
                ]
            )
            print("Wrote", woff2_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

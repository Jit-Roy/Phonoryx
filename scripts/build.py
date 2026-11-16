#!/usr/bin/env python3
"""Unified multi-target build orchestration script for Phonoryx.

Usage:
    python scripts/build.py --lite            # Build Lite standalone edition (~45 MB zip, no TTS)
    python scripts/build.py --full            # Build Full desktop edition (includes TTS & host AI bridging)
    python scripts/build.py --all             # Build both Lite and Full editions back-to-back
    python scripts/build.py --publish-lite    # Build Lite edition and upload to GitHub Release v2
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_cmd(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"\n[RUN] {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd or ROOT))
    if result.returncode != 0:
        print(f"[ERROR] Command failed with exit code {result.returncode}: {' '.join(str(c) for c in cmd)}")
        sys.exit(result.returncode)


def clean_duplicate_dlls(dist_dir: Path) -> None:
    """Ensure only the canonical _internal/phonoryx_engine.dll is kept."""
    redundant_paths = [
        dist_dir / "phonoryx_engine.dll",
        dist_dir / "assets" / "bin",
        dist_dir / "_internal" / "assets" / "bin",
    ]
    for p in redundant_paths:
        if p.is_file():
            p.unlink(missing_ok=True)
            print(f"  [CLEAN] Removed redundant file: {p.relative_to(dist_dir)}")
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            print(f"  [CLEAN] Removed redundant dir: {p.relative_to(dist_dir)}")


def create_zip(source_dir: Path, zip_dest: Path) -> int:
    """Create a zip archive using tar.exe or shutil.make_archive."""
    zip_dest.parent.mkdir(parents=True, exist_ok=True)
    if zip_dest.exists():
        zip_dest.unlink()

    print(f"\n[ARCHIVE] Compressing {source_dir.name} -> {zip_dest.name}...")
    tar_exe = shutil.which("tar")
    if tar_exe:
        run_cmd([tar_exe, "-a", "-c", "-f", str(zip_dest), "-C", str(source_dir.parent), source_dir.name])
    else:
        # Fallback to python shutil
        archive_base = str(zip_dest.with_suffix(""))
        shutil.make_archive(archive_base, "zip", root_dir=str(source_dir.parent), base_dir=source_dir.name)

    size_mb = zip_dest.stat().st_size / (1024 * 1024)
    print(f"[ARCHIVE SUCCESS] Created {zip_dest} ({size_mb:.2f} MB)")
    return zip_dest.stat().st_size


def build_lite() -> Path:
    print("\n" + "=" * 60)
    print(" BUILDING PHONORYX LITE EDITION")
    print("=" * 60)
    spec_path = ROOT / "Phonoryx-Lite.spec"
    dist_temp = ROOT / "dist" / "build_lite_tmp"
    work_temp = ROOT / "build" / "Lite"

    shutil.rmtree(dist_temp, ignore_errors=True)
    shutil.rmtree(work_temp, ignore_errors=True)

    run_cmd([
        sys.executable, "-m", "PyInstaller",
        str(spec_path),
        "--noconfirm",
        "--distpath", str(dist_temp),
        "--workpath", str(work_temp),
    ])

    app_dir = dist_temp / "Phonoryx"
    clean_duplicate_dlls(app_dir)

    # Archive to dist/Phonoryx-v2-windows-x64-lite.zip and dist/Phonoryx-v2-windows-x64.zip
    zip_out = ROOT / "dist" / "Phonoryx-v2-windows-x64.zip"
    zip_lite_named = ROOT / "dist" / "Phonoryx-v2-windows-x64-lite.zip"
    create_zip(app_dir, zip_out)
    shutil.copy2(zip_out, zip_lite_named)

    # Clean up temp build folder
    shutil.rmtree(dist_temp, ignore_errors=True)
    print(f"[COMPLETE] Phonoryx Lite packaged: {zip_out}")
    return zip_out


def build_full() -> Path:
    print("\n" + "=" * 60)
    print(" BUILDING PHONORYX FULL EDITION (LOCAL WORKSPACE)")
    print("=" * 60)
    spec_path = ROOT / "Phonoryx-Full.spec"
    target_dist = ROOT / "dist"
    work_temp = ROOT / "build" / "Full"

    shutil.rmtree(work_temp, ignore_errors=True)

    run_cmd([
        sys.executable, "-m", "PyInstaller",
        str(spec_path),
        "--noconfirm",
        "--distpath", str(target_dist),
        "--workpath", str(work_temp),
    ])

    app_dir = target_dist / "Phonoryx"
    clean_duplicate_dlls(app_dir)

    zip_out = target_dist / "Phonoryx-v2-windows-x64-full.zip"
    create_zip(app_dir, zip_out)

    print(f"[COMPLETE] Phonoryx Full executable ready at: {app_dir / 'Phonoryx.exe'}")
    print(f"[COMPLETE] Phonoryx Full archive ready at:    {zip_out}")
    return app_dir / "Phonoryx.exe"


def publish_lite() -> None:
    zip_path = build_lite()
    print("\n[PUBLISH] Publishing Lite distribution to GitHub Release v2...")
    pub_script = ROOT / "scripts" / "publish_release.py"
    run_cmd([sys.executable, str(pub_script)])


def main():
    parser = argparse.ArgumentParser(description="Phonoryx Dual-Edition Build Orchestrator")
    parser.add_argument("--lite", action="store_true", help="Build Lite edition (~45 MB zip, no TTS)")
    parser.add_argument("--full", action="store_true", help="Build Full edition (with TTS & host AI bridging)")
    parser.add_argument("--all", action="store_true", help="Build both Lite and Full editions")
    parser.add_argument("--publish-lite", action="store_true", help="Build Lite edition and upload to GitHub Release v2")

    args = parser.parse_args()

    if args.publish_lite:
        publish_lite()
    elif args.all:
        build_lite()
        build_full()
    elif args.lite:
        build_lite()
    elif args.full or len(sys.argv) == 1:
        # Default to full when no args specified
        build_full()


if __name__ == "__main__":
    main()

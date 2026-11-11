"""Inspect the local catalogue without sending audio."""

import argparse
import os

from .catalogue import CATEGORIES, catalogue
from .feature import ROOT


def main():
    parser = argparse.ArgumentParser(description="Check the local music catalogue without sending audio")
    parser.add_argument("--check", action="store_true")
    parser.parse_args()
    manifest = os.getenv("ROI_MUSIC_MANIFEST", str(ROOT / "docs" / "music_manifest.json"))
    directory = os.getenv("ROI_MUSIC_DIR", "/opt/roi-media/music")
    configured, playable = catalogue(manifest, directory)
    for category in CATEGORIES:
        print(f"{category}: {sum(x['category'] == category for x in playable)}/{sum(x['category'] == category for x in configured)} playable")


if __name__ == "__main__":
    main()

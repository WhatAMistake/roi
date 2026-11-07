"""Offline configuration check by default; --live creates one billable task."""

import argparse
from pathlib import Path
import sys
import uuid

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from tts_service import CloneError, clone_speech, configuration, reference_data_uri  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Create one potentially billable speech task")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    print("model=seed_audio (documented reference-audio route)")
    try:
        reference, timeout = configuration(require_verified=False)
        # Validate the exact local MP3 and prepare any trim without a network call.
        reference_data_uri(reference, timeout)
    except CloneError as exc:
        print(f"configuration={exc.category}; HTTP=not_called; code=none")
        return 2
    print("configuration=ready; local MP3 prepared; HTTP=not_called")
    if not args.live:
        print("HTTP=not_called; use --live to submit one potentially billable request")
        return 0
    print("live=enabled; sending the reference in the request body; one task may be billed")
    directory = ROOT / "temp_audio"
    directory.mkdir(exist_ok=True)
    output = directory / f"tts_probe_{uuid.uuid4().hex}.mp3"
    statuses = []

    def observe(status):
        if not statuses:
            print(f"submission_http={status}")
        statuses.append(status)

    try:
        duration = clone_speech("Hello, this is a short voice test.", output,
                                require_verified=False, observer=observe)
    except CloneError as exc:
        print(f"result={exc.category}; HTTP={exc.status or (statuses[-1] if statuses else 'not_received')}; code={exc.code or 'none'}")
        return 1
    print(f"result=valid_mp3; HTTP={statuses[-1]}; code=none; duration_seconds={duration:.2f}")
    print("Saved in temp_audio. Listen and verify voice identity before enabling; valid audio alone is not proof.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

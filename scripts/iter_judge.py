"""Check judge calibration by deciding each of the user's own reference photos.

They are the reference set, so P(alert) should be high for all of them. If it isn't, the
decision questions or the taste profile are too generic. Uses the production judge code and
a taste profile written by `scripts/iter_prompts.py taste --variant <variant>`. Calls the paid
Decisions API once per photo.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from pathlib import Path

from vsniper.core.config import Settings
from vsniper.domain.contracts import TasteProfile
from vsniper.integrations.openai.client import CandidateImageInput, OpenAITasteClient

ROOT = Path(__file__).resolve().parent.parent
PICS_DIR = ROOT / "pics_of_clothes_i_like"


def main(variant: str, limit: int) -> None:
    raw = json.loads((ROOT / "scripts" / f"out_taste_{variant}.json").read_text())
    flat_fields = ("taste_prompt", "likes", "dislikes_or_penalties", "instant_alert_examples",
                   "instant_reject_examples", "scoring_rubric", "transparency_labels")
    taste = TasteProfile.model_validate(
        {"summary": raw.get("core_aesthetic_summary", ""), **{key: raw[key] for key in flat_fields if key in raw}}
    )
    client = OpenAITasteClient(Settings(_env_file=ROOT / ".env"))
    try:
        for path in sorted(PICS_DIR.glob("*.jpg"))[:limit]:
            judgment = client.judge_candidate(
                taste_profile=taste,
                candidate=CandidateImageInput(
                    candidate_id=path.stem,
                    image_bytes=path.read_bytes(),
                    mime_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
                    title=path.stem,
                ),
                liked_anchors=[],
                disliked_anchors=[],
                manual_note=None,
            )
            print(f"{path.name}: {judgment.score}/100 labels={judgment.labels} concerns={judgment.concerns}")
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["current", "revised"], required=True)
    parser.add_argument("--limit", type=int, default=8, help="number of reference photos to decide")
    args = parser.parse_args()
    main(args.variant, args.limit)

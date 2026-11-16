import json
from pathlib import Path

from scanner.credits.credits_fixture_generator import generate_credits_fixture_corpus

CORPUS_PATH = Path(__file__).parent / "fixtures" / "mts" / "credits-evaluation-v1.json"


def test_generator_creates_bounded_legal_tail_manifests(tmp_path: Path):
    manifests = generate_credits_fixture_corpus(CORPUS_PATH, tmp_path)

    assert len(manifests) == 8
    for manifest_path in manifests:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert payload["version"] == "mts-credits-generated-tail-v1"
        assert payload["license"] == "CC0-1.0"
        assert payload["frames"]
        assert all((manifest_path.parent / item["path"]).is_file() for item in payload["frames"])

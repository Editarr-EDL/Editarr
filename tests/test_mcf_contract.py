import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from scanner.api import api_mcf_registry
from scanner.models import MCF_CATEGORY_REGISTRY, McfCategory

REGISTRY_PATH = Path(__file__).parent / "fixtures" / "mcf" / "category-registry-v1.json"
CLASSIFICATION_PATH = (
    Path(__file__).parent / "fixtures" / "mcf" / "editarr-classification-v1.json"
)


def test_mcf_supported_versions_share_one_complete_category_registry():
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    canonical_registry = json.dumps(
        registry, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()

    assert hashlib.sha256(canonical_registry).hexdigest() == (
        "31c026d28649e08e86036324ef6170a70c2463b469d0d13836bc0dc06540bcc2"
    )
    assert registry["source_versions"] == ["1.0.0", "1.1.0"]
    assert [family["token"] for family in registry["families"]] == [
        "commercial_content",
        "discrimination",
        "dispensable_scenes",
        "drugs",
        "fear",
        "language",
        "nudity",
        "sex",
        "violence",
    ]
    assert [len(family["categories"]) for family in registry["families"]] == [
        4,
        12,
        3,
        8,
        31,
        6,
        5,
        11,
        17,
    ]

    categories = [
        category
        for family in registry["families"]
        for category in family["categories"]
    ]
    tokens = [token for token, _label in categories]
    assert len(categories) == 97
    assert len(tokens) == len(set(tokens))
    assert all(token and label for token, label in categories)
    assert {"advertBreak", "nameCalling", "crueltyToAnimals", "weapons"} <= set(tokens)
    assert TypeAdapter(McfCategory).json_schema()["enum"] == tokens
    assert [
        {
            "token": family.value,
            "categories": [list(category) for category in categories],
        }
        for family, categories in MCF_CATEGORY_REGISTRY.items()
    ] == [
        {"token": family["token"], "categories": family["categories"]}
        for family in registry["families"]
    ]


def test_editarr_uses_mcf_taxonomy_and_frozen_initial_scan_mappings():
    contract = json.loads(CLASSIFICATION_PATH.read_text(encoding="utf-8"))

    assert contract["primary_fields"] == {
        "category": "McfCategoryFamily",
        "subcategory": "McfCategory",
        "severity": "Severity",
        "mcf_channel": "McfChannel",
        "mcf_comment": "string",
    }
    assert contract["new_findings_require_category_pair"] is True
    assert contract["legacy_edl_may_be_unclassified"] is True
    assert contract["producer_mappings"] == [
        {
            "producer": "ordinary_profanity",
            "category": "language",
            "subcategory": "swearing",
        },
        {
            "producer": "explicit_blasphemy",
            "category": "language",
            "subcategory": "blasphemy",
        },
        {"producer": "nudity", "category": "nudity", "subcategory": "nudity"},
        {
            "producer": "violence",
            "category": "violence",
            "subcategory": "violence",
        },
    ]
    assert contract["legacy_edl_aliases"] == {
        "profanity": ["language", "swearing"],
        "nudity": ["nudity", "nudity"],
        "violence": ["violence", "violence"],
    }
    assert contract["post_core_subcategorization"] == {
        "task": "MCF-12",
        "minimum_evidence_source": "qwen_summaries",
        "fallbacks": {
            "language": "swearing",
            "nudity": "nudity",
            "violence": "violence",
        },
    }


def test_frontend_registry_contract_uses_the_single_backend_authority():
    registry = api_mcf_registry()

    assert [family.token for family in registry.families] == list(MCF_CATEGORY_REGISTRY)
    assert sum(len(family.categories) for family in registry.families) == 97
    assert [
        (category.token.value, category.label)
        for family in registry.families
        for category in family.categories
    ] == [
        category
        for categories in MCF_CATEGORY_REGISTRY.values()
        for category in categories
    ]

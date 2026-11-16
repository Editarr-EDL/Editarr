from pathlib import Path

from scanner.mcf import McfCueEntry, McfParseResult
from scanner.models import (
    McfCategory,
    McfCategoryFamily,
    McfSourceVersion,
    ReviewSynchronization,
    ScanMcfDocument,
    Severity,
)

FIXTURES = Path(__file__).parent / "fixtures" / "mcf"



def _parsed_document(channels):
    return McfParseResult(
        source_version=McfSourceVersion.V1_1_0,
        source_metadata=ScanMcfDocument(
            source_version=McfSourceVersion.V1_1_0,
            material_start_seconds=0,
            material_end_seconds=10,
        ),
        entries=[
            McfCueEntry(
                cue_index=index,
                payload_index=0,
                line_number=7 + index * 3,
                start_seconds=index + 1,
                end_seconds=index + 2,
                category=McfCategoryFamily.VIOLENCE,
                subcategory=McfCategory.violence,
                severity=Severity.HIGH,
                mcf_channel=channel,
            )
            for index, channel in enumerate(channels)
        ],
    )


def _source_timing(parsed: McfParseResult):
    return {
        "synchronization": ReviewSynchronization(
            start_seconds=parsed.source_metadata.material_start_seconds,
            end_seconds=parsed.source_metadata.material_end_seconds,
            end_mode="before_credits",
        ),
        "mcf_material_end_seconds": parsed.source_metadata.material_end_seconds,
        "accepted_credits": None,
    }

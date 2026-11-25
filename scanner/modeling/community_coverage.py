"""Which categories a completed scan stage examined, for submission coverage (§5.2).

Coverage is per channel, not per model: a completed stage that analyzed a channel
covers every MCF family judged on that channel (`MCF_FAMILY_CHANNELS`), whichever
model ran. Other contributors' filters in those families that the scan did not
reproduce then receive implicit coverage negatives (§6.8).
"""

from scanner.modeling.model_core import MCF_FAMILY_CHANNELS, McfChannel

# Scan stages that analyze one media channel.
STAGE_CHANNELS = {"visual": McfChannel.VIDEO, "audio_scan": McfChannel.AUDIO}


def channel_categories(channel: McfChannel) -> tuple[str, ...]:
    """The sorted top-level families analyzed on `channel`."""
    return tuple(
        sorted(family.value for family, owner in MCF_FAMILY_CHANNELS.items() if owner == channel)
    )


def stage_categories(stage: str) -> tuple[str, ...]:
    """The families a completed `stage` covered; () for stages that analyze no channel."""
    channel = STAGE_CHANNELS.get(stage)
    return channel_categories(channel) if channel is not None else ()

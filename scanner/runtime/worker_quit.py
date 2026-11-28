"""Signal that a full application Quit stopped a dedicated scan worker."""


class FullApplicationQuitRequested(Exception):
    """A controller-owned Quit stopped this worker without cancelling its scan."""


class ScanPauseRequested(FullApplicationQuitRequested):
    """The job has a durable pause request in the queue."""


class ScanReservationLost(FullApplicationQuitRequested):
    """A stale worker has no authority to alter a replacement attempt."""

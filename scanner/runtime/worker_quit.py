"""Signal that a full application Quit stopped a dedicated scan worker."""


class FullApplicationQuitRequested(Exception):
    """A controller-owned Quit stopped this worker without cancelling its scan."""

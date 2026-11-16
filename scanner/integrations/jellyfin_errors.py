"""Errors the Jellyfin adapter raises, safe to return from Editarr's own API."""


class JellyfinError(Exception):
    """A safe error suitable for returning from Editarr's own API."""


class JellyfinNotFound(JellyfinError):
    """The provider confirmed that a requested catalog item does not exist."""

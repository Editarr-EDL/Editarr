"""Jellyfin account admission and sync (MU-16).

`states` decides each linked account's transition from one verification
outcome. `store` applies it. `admission` creates accounts at first sign-in and
by import. `sync` refreshes linked accounts. `evidence` supplies the linked
server's facts that media decisions need.
"""

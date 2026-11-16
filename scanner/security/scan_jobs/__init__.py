"""Authorize scan jobs, their history, and their workers for one account.

`media` decides what each scan read, `authority` decides whether a job's
initiator may still run it, and `JobGate` rechecks that inside a worker.
"""

"""Per-user filter defaults: live inheritance, independent copies, and enforced policy.

`values` holds what one user stores, `effective` derives what applies, and
`events` names policy changes for review and export consumers. The pure
selection and severity evaluation stays in `scanner.settings.filter_defaults`.
"""

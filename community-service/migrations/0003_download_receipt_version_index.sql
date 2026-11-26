-- Index download receipts by version so snapshot-byte retention (contract §8) finds the
-- receipts of each superseded revision without scanning the whole table. Data-preserving.
CREATE INDEX IF NOT EXISTS idx_download_receipts_version ON download_receipts(version_id, revision, issued_at);

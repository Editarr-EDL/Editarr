# CSS-15 base archive and optional encryption evaluation (nonproduction)

> Current local policy (October 7, 2026): usage-admission requirements in this dated
> evidence are superseded. The Docker service has no daily/hourly user allowances,
> request buckets, aggregate payload caps or usage-pressure pauses. Historical
> measurements are not new execution results. Use the [Docker contract](../../community-service-docker.md).

**September 27 policy update:** D06 makes encryption optional and off by default for
both local backup kinds, including selected credentials and keys. This experiment
now covers both plain and optional encrypted output. Plain mode has no passphrase
or age/Expect dependency. Its checksums detect damage, not deliberate rewriting.

## Recommendation for G-BACKUP

The user approved the **age v1 optional passphrase container** and the backup
recommendations on September 27, 2026, after making encryption opt-in and off by
default. Prefer a **bounded, uncompressed tar with an allowlisted JSON manifest**
as the shared base archive: write it directly for plain backups or feed its stream
through age for encrypted backups. This is conditional on a production integration
with a reviewed age library/sidecar, measured scrypt bounds, atomic output and
fully staged restore validation. The toy manifest/member names and limits in the
probe are not a release format. The local migration safety snapshots
in `scanner/backup/snapshots.py` remain separate and unencrypted.

Primary references reviewed: [age v1 specification](https://age-encryption.org/v1),
[age v1.3.2 API](https://pkg.go.dev/filippo.io/age@v1.3.2),
[age CLI and installation](https://github.com/FiloSottile/age/tree/v1.3.2),
[age v1.3.2 Linux ARM release artifacts](https://github.com/FiloSottile/age/releases/tag/v1.3.2),
[Raspberry Pi 3 Model B specifications](https://www.raspberrypi.com/products/raspberry-pi-3-model-b/),
[Python tarfile streaming and extraction safety](https://docs.python.org/3/library/tarfile.html),
[7z format](https://www.7-zip.org/7z.html), and
[py7zr guide](https://py7zr.readthedocs.io/en/latest/user_guide.html).

| Choice | Benefits | Costs / unresolved proof |
| --- | --- | --- |
| Uncompressed tar + JSON manifest (plain default) | Python stdlib `w|`/`r|` streaming; no passphrase, age or extra native dependency. One base content inventory for both modes. | Readable secrets/identity and manifest; SHA-256 checksums are not authentication. Generic tar supports links, duplicate paths and special files, so a production reader needs an allowlist, limits and isolated staging; never use unrestricted `extractall()`. |
| age v1 (Go CLI/library or interoperable rage) | Specified scrypt stanza with fresh 16-byte salt, authenticated header, 64 KiB authenticated chunks and final-chunk truncation detection; supports streaming. Go API provides `ScryptIdentity.SetMaxWorkFactor`. | A second native executable or Go integration is needed; CLI passphrase prompt is unsuitable as a production Python subprocess API. age does not define the inner manifest/archive, file allowlist or atomic restore. |
| 7z AES-256 (7-Zip/py7zr) | Familiar archive and optional encrypted headers; packages exist for major OSes. | 7z AES/CRC does not provide the same documented chunk-level AEAD guarantee as age; archive parsing and decompression increase untrusted restore surface. py7zr adds PyCryptodomex, compression extensions and platform-specific dependencies. KDF and extraction limits need independent proof. |
| ZIP AES (Python bindings) | Broad archive tooling. | AES ZIP variants and implementations differ; no reviewed binding with the required bounded KDF and authenticated streaming extraction was established in this task. Not recommended without separate proof. |

The installed Homebrew age 1.3.2 ARM bottle was 5.4 MB compressed and 12.4 MB
installed, with no runtime formula dependencies; this is **not** a release size
measurement. age documents prebuilt Windows, Linux and macOS binaries. Bundling,
signing, platform architectures and installed-release tests remain unverified.
The existing `cryptography`/`argon2-cffi` dependencies do not themselves define
a reviewed encrypted streaming container; composing one would invent a protocol.
`pyrage` documents byte-buffer passphrase APIs, not a verified streaming Python
API, so it was not used for this measurement.

## Disposable proof

Run `.venv/bin/python scripts/backup_age_evaluation.py --plain --kind full` (or
`--kind settings`) with no age installed; omit `--plain` to test age 1.3.2 with
macOS `expect`. This is a **synthetic experiment**, not a supported backup file,
release dependency or application route. A 64 MiB full fixture and a 1 MiB
settings fixture share the same tar writer, allowlisted reader and JSON manifest.
The manifest records only dummy kind, size and SHA-256. The reader bounds the
manifest to 4096 bytes (also tests rejection at 4097), requires exactly two regular members and
streams the synthetic data checksum. No production credentials or database are
used. For optional encryption, a private FIFO feeds this same tar into age; only
the age header/KDF stanza is outside ciphertext. The age v1 scrypt stanza
supplies fresh random salt.
The experimental reader rejects work factors above log2 N = 18 before invoking
age (about 256 MiB scrypt working memory at r=8); the user subsequently approved
this cap for production restore. age's library default maximum is 22, too high
to accept uncritically from an untrusted archive. The probe's header parsing is
not a complete specification-compliant parser and must not be reused for restore.

On macOS ARM, plain 64 MiB produced 67,112,960 tar bytes. With age removed from
`PATH`, `/usr/bin/time -l` reported 0.21 s wall time and 37,683,200 bytes
maximum RSS for the plain full round-trip **including two negative copies**;
the measured archive write/check alone took 0.11 s. The 1 MiB plain settings
fixture produced 1,054,720 tar bytes. Both plain fixtures rejected changed data
and mid-body truncation; a malicious author can rewrite the manifest/checksums
and the probe does not detect appended garbage after tar's end marker. Plain
backups expose all selected identity and credentials to anyone who can read them.

For encrypted full mode, 64 MiB plaintext produced 67,129,526 ciphertext bytes. The
round-trip plus negative checks took 2.28 s (one observed run); `/usr/bin/time -l`
reported 347,783,168 bytes maximum resident set size and 18,793,000 bytes peak
memory footprint for the measured process tree. These figures include scrypt,
age, Expect and Python and are not a cross-platform performance guarantee. A
repeat after the shared-reader change took 2.30 s wall time and 347,652,096
bytes maximum RSS. Encrypted settings mode produced 1,055,158 ciphertext bytes.
Wrong dummy password, modified payload, truncated final chunk and modified
work factor 30 were rejected; the experiment removed partial plaintext output.
The encrypted manifest round-tripped and its digest matched the synthetic member.
The writer does not hold the entire 64 MiB source in memory, but the experiment
copies ciphertext for negative cases and decrypts into a private temporary tar.
Production restore must stage a complete generation, enforce archive entry and
expanded-size limits, verify the final chunk, manifest, DB integrity and key
allowlist **before** activation, and clean every partial output on all failures.

Threat limits: this protects portable files at rest against an attacker without
the passphrase, not against compromise of the running process, weak passphrases,
maliciously chosen backup contents, rollback, or disclosure of backup existence
and size. The unencrypted age stanza exposes version, recipient type, salt and
work factor. File paths, identity and secret inventory must stay inside ciphertext.
Do not pass real passphrases via argv, environment variables, Expect scripts,
logs or prompts exposed to automation. A production library/API with controlled
passphrase lifetime, private temporary storage and error propagation is still
required; this proof uses only the fixed public fixture password.

## Decisions and remaining questions

The user approved optional age v1 encryption and the recommended shared base
archive in principle; implementation may bundle a reviewed age integration for
encrypted mode, but must never depend on it for plain mode. An interactive age
CLI/Expect prompt is not a production Python subprocess API. Native release
packaging and a safe passphrase interface still need proof.

1. The user clarified that the restore target is a **Raspberry Pi 3**, with
   no physical board available. The original Model B has a 1.2 GHz quad-core
   CPU and 1 GB RAM (B+ is 1.4 GHz, also 1 GB). Keep the previously requested
   10-second passphrase unlock as a design target, not a demonstrated guarantee.
   age v1.3.2 publishes Linux ARM and ARM64 binaries; no Pi installation or
   native-release test was run. `scripts/backup_scrypt_benchmark.py` measured
   the same scrypt parameters (N=2^logN, r=8, p=1, 32-byte result) with OpenSSL
   via Python on an Apple M4 Pro, **not** the age Go implementation on a Pi:

   | log2 N | scrypt array | Mac median (3 runs) | Mac peak RSS |
   | --- | ---: | ---: | ---: |
   | 14 | 16 MiB | 0.019 s | 37,371,904 bytes |
   | 15 | 32 MiB | 0.039 s | 53,968,896 bytes |
   | 16 | 64 MiB | 0.079 s | 87,556,096 bytes |
   | 17 | 128 MiB | 0.163 s | 154,632,192 bytes |
   | 18 | 256 MiB | 0.323 s | 289,030,144 bytes |

   Each step roughly doubles work and memory. The user chose **age's default
   logN=18 for writes and a maximum accepted restore factor of 18**, superseding
   the earlier factor-15 suggestion. Its scrypt array is 256 MiB; the Mac probe
   peaked near 289 MB for the KDF alone, and the encrypted archive experiment
   peaked near 348 MB for its process tree. Those are not Pi 3 measurements or
   upper bounds on the complete application. On a 1 GB Pi 3, memory pressure,
   Go implementation differences, 32-bit versus 64-bit OS, other services,
   SD-card load and thermal throttling can affect both success and speed. No
   Pi-class unlock test was available, so the 10-second goal is **unverified**;
   a slower unlock or memory failure must be reported, never downgraded to plain
   output. Restore must reject factors above 18 before expensive KDF work with
   a clear unsupported-work-factor message. The experiment already exercises
   this by rejecting factor 30.
2. Owner-only local pre-upgrade snapshots have user-selected automatic retention
   of 3 generations, 3 days and 5 GiB total. Always retain the last verified
   pre-migration generation, even if a limit is exceeded. Refuse an upgrade
   when a fresh recoverable snapshot cannot be created within available space;
   never delete the only verified recovery generation to reclaim space. No at-rest
   encryption or credential-store policy is chosen for these local snapshots.

3. The user approved initial restore ceilings of 256 regular-file members,
   1 MiB for the manifest, 256 UTF-8 bytes per member path, 64 GiB for the
   SQLite database member, 64 MiB per other member (except the stricter manifest
   cap), and 80 GiB total member
   content. These count files inside the archive, not scans, reviews, titles
   or events inside SQLite. Apply the same ceilings to plain and age-encrypted
   backups; stream checksums and enforce both individual and aggregate sizes
   before activation. Reject duplicate/unexpected paths, links, special files
   and trailing bytes, and verify staging disk space. If data cannot fit,
   report an explicit error without publishing a partial backup or touching
   active data. The probe's two-member and 4096-byte manifest bounds are only
   fixtures and do not test these production ceilings.

No unattended portable backup or saved export passphrase policy is selected.
G-BACKUP design choices for CSS-16/17 are resolved; production parsing,
passphrase integration, Pi timing and native packaging remain unproven.

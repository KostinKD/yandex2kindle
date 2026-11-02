# KOReader statistics and WebDAV

Implement the approach accepted in the conversation: manual SQLite upload followed
by a dedicated WebDAV endpoint in the existing FastAPI app. No external service or
additional dependency. Work in the existing checkout; leave changes reviewable.

1. Add integration tests using real SQLite fixtures and the real authentication
   middleware. Cover upload, repeat/extended imports, malformed input, timezone
   boundaries, unmatched books, tenant isolation and conditional WebDAV writes.
2. Implement bounded, read-only validation of KOReader's actual `book` and
   `page_stat_data` tables. Preserve imported history independently of the library.
   Match by binary checksum, falling back to unambiguous title/author. Merge page
   records by book identity/page/start time using maximum duration, as KOReader
   does. Never infer exact reading time from progress synchronization.
3. Provide `/webdav/` with Basic authentication using website credentials, root
   listing and a single `statistics.sqlite3` resource. Support OPTIONS, PROPFIND,
   GET, HEAD, PUT and DELETE, strong ETags and If-Match/If-None-Match. Persist the
   received snapshot and imported history in one SQLite transaction. This is a
   statistics receiver, not general-purpose file hosting. DELETE removes the
   remote snapshot only; imported history remains.
4. Add manual upload and connection instructions to `/stats`, daily calendar,
   per-book time, sessions and import status. Keep estimated web activity labeled
   separately from measured KOReader records. Split reading across midnight in
   READING_TIMEZONE (default Europe/Moscow).
5. Run the full unittest suite in the existing Docker image, request a code
   review, fix substantial findings, rebuild and restart the local service, then
   verify health/authenticated WebDAV discovery without altering user history.

Verification: use fixtures modeled on KOReader upstream SQLite schema and its
cloudstorage WebDAV provider's GET → merge → conditional PUT sequence. Real Kindle
upload remains a user-device verification. Historical deletions are not propagated
to imported analytics; a smaller snapshot cannot erase previously received history.

Review: independent reviewer checked current upstream schema and WebDAV sequence,
tenant isolation, ETags, transactional persistence and analytics. Fixed generated
columns/SQLite value allocation limits and out-of-range calendar timestamps, each
with a failing regression test. Also fixed fractional-second loss when estimated
web reading spans midnight, with a failing regression test. Baseline 119 tests;
feature suite adds 15 real middleware/SQLite integration tests. Full suite: 134
tests passed after review fixes.

Deployment: backed up owner/accounts SQLite databases under
`data/backups/before-koreader-stats-20261101-143901/`. Reused existing dependency
image (requirements unchanged), copied final app code and restarted Compose.
Verified HTTP health 200, anonymous WebDAV 401 with Basic challenge, root
discovery 207, Moscow timezone, desktop/mobile browser rendering without JS
errors or horizontal overflow. Final full suite: 134 tests, OK.

# Fixtures

These are derived dummy-data fixture specifications, not wholesale copies of official assets and never real taxpayer data. `positive-minimal.json` and `negative-cases.json` are source-derived expectations until the corresponding live-UI observations are recorded.

The structural audit enforces unique negative-case IDs and requires every fixture rule_id to resolve to a tracked validation rule.

`calculation-boundaries.json` records 66 passing executions of exact hash-pinned 7.9.6 JScript: Item 46 threshold-adjacent cases for both parties and signed Item 45 sums. It is headless runtime evidence, not a live-UI observation.

`checksum-cases.json` records synthetic black-box executions of the hash-pinned virtualized `chkt.exe`, including malformed inputs, valid/invalid checksums, a suffix sweep, and proof that `999999999` is rejected by the helper but accepted by the JavaScript wrapper.

`encryption-roundtrip.json` records deterministic virtualized `Encrypt.exe` output and byte-exact recovery through the repository crypto algorithm. It does not claim the live Final Copy UI or its flag transition was observed.

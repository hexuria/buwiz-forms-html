# Overlay inventories

These snapshots are join/leftover fields.json inventory only.

They must not live under rules/forms/. That directory is the locked
43-form v1 corpus (EXPECTED_V1_FORM_MANIFEST_COUNT = 43). A 44th
directory there fails validation-rules-v2.

2000-dst-v2018 exists so catalog slug 2000-dst-2018 (stem 2000dst)
resolves without stealing live 2000-v2018 (stem 2000).

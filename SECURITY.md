# Security and privacy

DociFi processes highly sensitive personal data (names, dates of birth, Social Security numbers, addresses).
It is designed so that this data stays on the user's computer.

## Guarantees built into the code

| Area | What DociFi does |
|---|---|
| **No network** | The code makes no network calls. Both OCR engines and their models run locally and are bundled. Nothing is uploaded, and there is no telemetry, no update check and no cloud service. |
| **Originals untouched** | Input folders are only read. Converted copies are written to the results folder. |
| **SSN masking** | SSNs are masked to the last four digits (`***-**-1234`) in the check sheet, the app, the console and logs. Full numbers exist only in memory, for matching. |
| **Logs without personal data** | The audit trail (`logs/audit.jsonl`) records file IDs, page counts, decisions and timings. Fields such as name, DOB, SSN, address, text, label, path and file name are withheld automatically. |
| **Redacted copy** | *Hide names, dates of birth and addresses* produces a check sheet that can be shared: personal values hidden, no links to the documents. |
| **Duplicates** | Possible duplicates are flagged for review and never deleted automatically. |
| **Secure delete** | Removes a results folder after review. It only accepts folders created by DociFi (marker file), refuses drives, system and user-profile folders, and requires the folder name typed back. Files are overwritten with random data, flushed, renamed and then deleted. |

## Honest limits

- On SSDs, overwriting a file cannot guarantee that every physical copy is erased, because of wear levelling.
  Use full-disk encryption (BitLocker) for reliable protection of data at rest.
- The OCR cache (`.pcs_cache` inside the results folder) holds page text so repeat runs are fast. It is removed by
  secure delete together with the results.
- Labeled PDFs and the unredacted check sheet necessarily contain personal data. Store them like the original file.

## This repository

No client documents, outputs or real personal values are stored here. `.gitignore` excludes `Data/`, `input/`,
`output/`, spreadsheets, PDFs and images outside `docs/`. Before publishing, an automated scan compared every
committed file against the values in the real sample file (names, dates of birth, SSNs, addresses) and against the
example names in the guide workbook, and found none. All test values are invented.

## Reporting a problem

Please report security concerns privately to the repository owner rather than in a public issue.

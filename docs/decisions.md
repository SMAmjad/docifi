# Design decisions

Short records of the choices that shape DociFi, with the reason and the trade-off.

### 1. Local desktop app, no cloud
**Decision:** Everything runs on the user's PC; no API, no cloud OCR, no AI service.
**Why:** Loan files contain SSNs and dates of birth. Keeping them local removes a whole class of risk
(transfer, storage and vendor access) and needs no data-processing agreement.
**Trade-off:** Accuracy and speed depend on local OCR and the PC's CPU.

### 2. Engine first, UI second
**Decision:** A UI-free engine with a CLI was built and verified before the desktop app.
**Why:** It could be tested end to end on real files without a UI. The same engine can later run as a batch
job, a watch-folder service or behind a server without rewriting.

### 3. Rules in YAML, not in code
**Decision:** Document types, keywords, page order, field anchors and file-name templates live in
`document_types.yaml`.
**Why:** Naming conventions change and every company has its own. A rule change should not need a developer
or a new release. The `selftest` command checks the rules against a guide workbook.

### 4. Two OCR engines, second one on demand
**Decision:** Tesseract for every scanned page; RapidOCR only when quality is below 70 or a needed value is missing.
**Why:** Running both on every page took about 8 minutes on the sample file; on demand it takes about 5–6.
Photos of ID cards read clearly better with the deep-learning engine, so those types use its reading first.

### 5. Never guess, send to review
**Decision:** Unreadable or low-confidence values become *Check manually*. Blank and unrecognised pages go
to a review folder. Duplicates are flagged, never deleted.
**Why:** A wrong "Correct" is worse than an extra manual check. The tool's job is to point the processor to
exactly what needs human eyes.

### 6. Reference value = most trustworthy single source
**Decision:** For each person and field, the reference comes from the highest-weighted source (e.g. ID card
for DOB), not from a majority vote.
**Why:** Two tax returns prepared from the same wrong data would outvote the licence. This exact case occurred
in the sample file.

### 7. One-digit SSN difference on a scan = *Check manually*
**Why:** On scans this is almost always an OCR misread. Reporting it as a mismatch would cause false alarms,
and ignoring it would hide a real typo. A human glance settles it.

### 8. PyMuPDF behind one module
**Decision:** Only `engine/pdf_backend.py` imports PyMuPDF.
**Why:** PyMuPDF is fast and complete but AGPL-licensed. For commercial sale it must be licensed from Artifex or
replaced. Isolating it makes the swap a one-file job (pypdfium2 + pikepdf are candidates).

### 9. Single-file .exe, no installer
**Decision:** PyInstaller one-file build with Tesseract and RapidOCR inside.
**Why:** The user receives one file and runs it. No admin rights, no Python, no Tesseract installation.
**Trade-off:** About 5 s start-up, because the .exe unpacks itself on every launch. The loader shows the splash
immediately so the wait is visible and branded. An installer or a one-folder build would start in about 2 s.

### 10. Privacy rules enforced in code, not just policy
**Decision:** SSN masking happens in the data model (`SSN.masked`), and the audit log withholds personal
field names automatically. A redacted export exists for sharing.
**Why:** Policies are forgotten; code paths are not.

### 11. Guide 1 is a rulebook, not an answer key
**Finding:** The provided guide lists 78 example file names for other clients' documents. It defines the
naming style; it does not describe the files in the sample. DociFi uses it to test its own rules: all 78 example
types map to a rule; 67 names are reproduced exactly or with the same content; the 11 that differ are
inconsistencies inside the guide itself (for example a missing separator or a different date style), each listed
with the reason in the Self-Test tab.

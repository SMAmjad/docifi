# Changelog

## Unreleased

- Several documents folders in one run: choose any number of folders (multi-select in Browse, or add more);
  one reading pass over all pages; each folder gets its own `documents/` and `Check_Sheet.xlsx` inside the run folder
  and is checked on its own. A single folder keeps the original layout. CLI: `run --input <folder> <folder> ...`.
- New "Role on document" column (app and check sheet): e.g. "Spouse - joint with ..." on a joint 1040, so it is
  clear why one person appears on a document named after another.
- Results folder defaults to the app's own `output` folder (Browse to choose any folder, Default to go back);
  the documents folder list starts empty.
- 130 automated tests.

## 1.0.0 — 2026-11-10

First release.

- Conversion of images (incl. multi-page TIFF), Word, RTF/ODT and text files to PDF.
- Page reading: PDF text layer, Tesseract OCR with orientation detection, image clean-up and rotation retries,
  RapidOCR second reading on demand, reading cache.
- Recognition of 17 document types from YAML rules; 1040 packages grouped by tax year in IRS page order;
  continuation pages kept with their document; several ID cards on one page cut out individually.
- Extraction of Name, Date of Birth, SSN and Address; people grouping; reference values from the most
  trustworthy source; Correct / Issue / Check manually per field; separate home and property address checks.
- Labeled PDFs per person; Excel check sheet with 8 tabs (Document Check, People, Review Queue, Duplicates,
  Files, Pages, Self-Test, About); redacted mode; audit log without personal data.
- Optional naming-rule self-test against a guide/rules workbook.
- Secure delete of result folders with safeguards.
- Desktop app (PySide6): animated start-up, light and dark themes, results table with filters, search and details.
- Command line: `run`, `selftest`, `envcheck`, `secure-delete`.
- Single-file `DociFi.exe` with Tesseract and RapidOCR bundled.
- 123 automated tests.

"""Several documents folders in ONE run: each folder gets its own documents and check sheet.

The input files are digital PDFs generated inside the test with invented values (no OCR needed).
"""

import pytest

from pcs_tool.config import load_config
from pcs_tool.engine import pdf_backend
from pcs_tool.engine.pipeline import PipelineError, folder_labels, run_pipeline

W2 = """Form W-2 Wage and Tax Statement 2025
OMB No. 1545-0008 Copy B
a Employee's social security number 123-45-6789
c Employer's name, address, and ZIP code
EXAMPLE DENTAL LLC
100 COMMERCE ST
AUSTIN TX 78701
e Employee's name, address, and ZIP code
JANE Q DOE
45 OAK DR
HOUSTON TX 77001
1 Wages, tips, other compensation 50000.00"""


@pytest.fixture(scope="module")
def multi_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("multi")
    a, b = root / "Borrower A", root / "Borrower B"
    a.mkdir()
    b.mkdir()
    pdf_backend.text_to_pdf(W2, a / "w2.pdf")
    (b / "same w2.pdf").write_bytes((a / "w2.pdf").read_bytes())   # the same file in both folders
    out = root / "Results"
    return a, b, out, run_pipeline([a, b], out, cfg=load_config(), workers=1)


def test_one_run_with_a_check_sheet_per_folder(multi_run):
    _, _, _, result = multi_run
    assert set(result.sheets) == {"Borrower A", "Borrower B"}
    for label, sheet in result.sheets.items():
        assert sheet == result.run_dir / label / "Check_Sheet.xlsx" and sheet.is_file()
    assert (result.run_dir / "logs" / "audit.jsonl").is_file() and (result.run_dir / "run_summary.json").is_file()
    assert set(result.stats["folders"]) == {"Borrower A", "Borrower B"}


def test_documents_are_saved_inside_their_own_folder(multi_run):
    _, _, _, result = multi_run
    for d in result.documents:
        assert d.output_path.is_relative_to(result.run_dir / d.folder / "documents")
        assert d.label == "Jane - INCOME - W2 - EXAMPLE DENTAL LLC - 2025"


def test_folders_are_checked_separately(multi_run):
    _, _, _, result = multi_run
    # The same file in two folders is not a duplicate across folders, and each folder has its own person.
    assert not any(f.duplicate_of for f in result.files)
    assert not any(d.duplicate_of for d in result.documents)
    assert len(result.persons) == 2
    folders = {p["person"].person_id: {d.folder for d, _ in p["person"].records} for p in result.persons.values()}
    assert all(len(f) == 1 for f in folders.values())


def test_folder_inside_another_chosen_folder_is_refused(tmp_path):
    (tmp_path / "in" / "sub").mkdir(parents=True)
    with pytest.raises(PipelineError, match="inside another"):
        run_pipeline([tmp_path / "in", tmp_path / "in" / "sub"], tmp_path / "out", cfg=load_config())


def test_single_folder_keeps_the_original_layout(tmp_path):
    (tmp_path / "in").mkdir()
    pdf_backend.text_to_pdf(W2, tmp_path / "in" / "w2.pdf")
    result = run_pipeline([tmp_path / "in"], tmp_path / "out", cfg=load_config(), workers=1)
    assert result.sheets == {} and result.sheet_path == result.run_dir / "Check_Sheet.xlsx"
    assert result.documents[0].output_path.is_relative_to(result.run_dir / "documents")


def test_same_folder_names_get_a_number(tmp_path):
    (tmp_path / "x" / "Docs").mkdir(parents=True)
    (tmp_path / "y" / "Docs").mkdir(parents=True)
    assert folder_labels([tmp_path / "x" / "Docs", tmp_path / "y" / "Docs"]) == ["Docs", "Docs (2)"]


def test_role_column_explains_joint_tax_returns():
    from datetime import date

    from pcs_tool.engine.normalize import parse_ssn
    from pcs_tool.engine.report import document_rows
    from pcs_tool.engine.verify import verify
    from pcs_tool.models import Document, Page, PersonRecord, RunResult

    cfg = load_config()
    d = Document("D001", "personal_tax_return", "Personal Tax Return", [Page("F001", 0, method="text layer")])
    d.persons = [PersonRecord("primary taxpayer", "John K Doe", parse_ssn("123-45-6789"), date(1980, 1, 2)),
                 PersonRecord("spouse", "Jane S Doe", parse_ssn("223-45-6780"), date(1982, 3, 4))]
    people = verify([d], load_config())
    result = RunResult(run_dir=__import__("pathlib").Path("."), files=[], pages=d.pages, documents=[d],
                       persons={p.person_id: {"person": p} for p in people})
    roles = {r["person"]: r["role"] for r in document_rows(result, people, cfg)}
    assert roles == {"John K Doe": "Primary taxpayer - joint with Jane S Doe",
                     "Jane S Doe": "Spouse - joint with John K Doe"}

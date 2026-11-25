"""R2026.43: the verify-metadata form replaced its second checkbox by a required choice; both forms must be read."""
import importlib.util
import pathlib

SPEC = importlib.util.spec_from_file_location("ingest_verifications", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ingest_verifications.py")
iv = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(iv)


def _issue(n, body, labels=(), title="[verified] PRJNA1"):
    return dict(number=n, title=title, body=body, labels=[dict(name=x) for x in labels], user=dict(login="u%d" % n), created_at="2026-11-25T00:00:00Z")


OLD = "### Project accession\n\nPRJNA1\n\n### What I checked\n\n- [x] I searched the web\n- [x] As far as I can tell, the sample metadata shown on the project page is complete\n"
NEW_OK = "### Project accession\n\nPRJNA1\n\n### What I checked\n\n- [x] I searched the web\n\n### What did you find?\n\nNothing public is missing\n"
NEW_MISSING = "### Project accession\n\nPRJNA1\n\n### What I checked\n\n- [x] I searched the web\n\n### What did you find?\n\nI found something missing (I will also use Share metadata)\n"
NEW_UNTICKED = "### Project accession\n\nPRJNA1\n\n### What I checked\n\n- [ ] I searched the web\n\n### What did you find?\n\nNothing public is missing\n"


def test_both_forms_count_and_a_report_of_missing_metadata_does_not():
    rows = iv.rows_from_issues([_issue(1, OLD), _issue(2, NEW_OK), _issue(3, NEW_MISSING), _issue(4, NEW_UNTICKED), _issue(5, NEW_OK, labels=["invalid"])])
    assert [r["issue"] for r in rows] == ["1", "2"]

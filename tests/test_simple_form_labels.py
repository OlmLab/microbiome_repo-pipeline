"""R2026.43: the two-question finding form renamed its accession field; both labels must reach `identifier`."""
import pathlib

import yaml

from catalog import ingest_issues as ii

FORM = pathlib.Path(__file__).resolve().parents[1] / "site_generator" / "gen" / "issue_templates" / "simple-finding.yml"


def _body(labels):
    return "".join(f"### {lab}\n\n{val}\n\n" for lab, val in labels)


def test_old_and_new_accession_labels_parse():
    for lab in ("accession (filled automatically — leave as is)", "Project accession"):
        out = ii.parse_simple_form(_body([("What is wrong or missing?", "a value is wrong"), ("Tell us more", "x"), (lab, "PRJNA1")]))
        assert out["identifier"] == "PRJNA1" and out["current_state"] == "a value is wrong", (lab, out)


def test_every_input_label_of_the_installed_form_is_mapped():
    form = yaml.safe_load(FORM.read_text(encoding="utf-8"))
    labels = [b["attributes"]["label"] for b in form["body"] if b.get("type") != "markdown"]
    for lab in labels:
        assert any(lab.startswith(k) for k, _ in ii.SIMPLE_LABELS), lab

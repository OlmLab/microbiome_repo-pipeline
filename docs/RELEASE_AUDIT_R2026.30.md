# Release check — blind audit and recall recovery (R2026.30)

The stage-1 verdict for each project came from two independent readings at the utility tier, with an Opus-5 tie-break
where they disagreed. This check asks a **different, higher tier** (Opus 5, role `audit` in config/models.yaml) to
classify a random sample of projects from the same evidence, without showing it the stage-1 answer.

## Blind sample (112 projects, stratified by verdict)

| Stage-1 verdict | Projects audited | Agreement on in-scope vs out |
|---|---:|---:|
| no | 58 | 91.4% |
| partly | 10 | 80.0% |
| yes | 44 | 97.7% |
| **all** | **112** | **92.9%** |

Exact agreement including the yes/partly distinction was 88.4%; no reading refused.

The important asymmetry: among projects stage 1 called **in scope**, the auditor agreed 97.7% of the time —
so false *inclusions* are rare. Among projects called **out of scope**, it disagreed 8.6% of the time,
all in one direction: projects that should have been kept. Those disagreements were concentrated in a few categories —
ancient dental calculus, clinical sequencing direct from a specimen, and projects whose archive record says "amplicon"
but whose reads are shotgun.

## Targeted recovery

Rather than re-classify all 2,666 excluded projects at the audit tier, the recheck was aimed at the
population where the disagreements lay: excluded for `amplicon`, `isolate_genomes`, `single_cell`, `in_vitro`,
`environmental` or `other`, **and** with a read check that calls the reads shotgun. That is 250 projects, all of
which were re-classified.

* **37 projects were recovered** (15% of the rechecked set) and are now in scope, with status
  `audit_recovered` and the audit's evidence recorded. Of these, 14 were
  labelled amplicon in the archive but sequence as shotgun, 8 are
  clinical shotgun libraries from a single specimen rather than cultured isolates, and
  11 were mislabelled in other ways — one is an HMP buccal sample in a project
  titled as a plant transcriptome.
* Their samples were harvested (31,019 attribute rows for 1,759 specimens), their new attribute values
  mapped, and the scope rebuilt. 480 single-cell genome runs belonging to two of them were excluded: the project is in
  scope, that data set is not.

## What this means for the release

The in-scope set is now **2,255 newly classified projects** on top of the 2,757 gut projects. The audit implies the
remaining recall gap is small but not zero: out-of-scope projects outside the rechecked population were not re-examined,
and a further pass over, say, the `animal_host` and `human_genome` exclusions would be the way to close it. Precision is
the stronger result — the auditor confirmed 97.7% of inclusions.

Audit cost: $3.22 for the blind sample, 7.25 for the recheck (3 sub-agents), $0.02 for the new pair map.

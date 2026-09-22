# Xcelium IMC coverage merge contract

ZDDV treats Xcelium coverage as an evidence-first flow.

A coverage-enabled Xcelium run is eligible for merge only when the run-specific
coverage directory actually contains a native `.ucd` artifact. The merge layer
does not fabricate missing coverage databases.

For multiple captured runs, ZDDV invokes Cadence IMC in command mode and asks
IMC to:

1. merge the run databases with `-metrics all -initial_model union_all`;
2. load the merged run;
3. emit `report -summary -cumulative on -inst -local off`.

The merge is accepted only when IMC exits successfully and the merged run
contains both native `.ucd` and `.ucm` artifacts. The complete IMC stdout is
retained in `.zddv/coverage/xcelium/summary.txt`, and the command, inputs,
status, and normalized result are recorded in
`.zddv/coverage/xcelium/xcelium-coverage.json`.

## Normalization boundary

ZDDV currently normalizes only the first simulator-reported overall percentage
from an IMC summary table whose header contains `name` and `Overall`.
That value is stored in the percentage-native coverage-score history.

If the merge succeeds but the summary format is not recognized, ZDDV keeps the
native merged database and report evidence but does **not** create a numeric
coverage snapshot. Per-metric IMC normalization remains a separate milestone.

## Cadence references

- Cadence Community: "how to merge the coverage report using IMC?" documents
  batch IMC merge, loading the merged run, and report generation:
  https://community.cadence.com/cadence_technology_forums/f/functional-verification/20269/how-to-merge-the-coverage-report-using-imc
- Cadence Community: "Tutorial on NCsim" shows
  `report -summary -cumulative on -inst -local off` and the resulting
  `Overall*` summary table:
  https://community.cadence.com/cadence_technology_forums/f/functional-verification/35689/tutorial-on-ncsim

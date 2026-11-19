# Results database

Every stored case can be kept in one Azure SQL database, so comparisons across runs, models
and policies are queries rather than a new script per run folder. The database is the
free-tier serverless Azure SQL Database the infrastructure repository deploys
(`sqldb-dendro-results`); it pauses after 15 idle minutes and stops at the monthly free
allowance instead of billing.

## What goes in

| Table | One row per | Written by |
| --- | --- | --- |
| `dendro.cases` | stored case, with the whole record as JSON in `record` | control function (cluster runs), `sync_results_sql.py` (local runs) |
| `dendro.decisions` | subject a case decided on | same as `cases` |
| `dendro.labels` | unit of a labels file under `runs/` | `sync_results_sql.py` |
| `dendro.units` | unit a run folder ran: which case, which arm, which labels | `sync_results_sql.py` |
| `dendro.grades` | labelled case, graded by `evaluation.label_scoring` | `sync_results_sql.py` |

Views: `dendro.unit_results` (one row per unit with label, grade and the target's verdict)
and `dendro.collection_summary` (outcomes per collection, the wrong-genus rate among
single-genus names and the upper end of its 95% Wilson interval).

The schema is `scripts/batch/results-schema.sql`; the rows come from
`scripts/batch/results_rows.py`, which the control function copies in at deploy, so a case
gets the same key (`cluster:<run_id>/<case_id>` or `local:<collection>/<unit>`) whichever
writer stores it first.

## Using it

```
pip install -e ".[results-sql]"
az login
python scripts/batch/sync_results_sql.py --server <server>.database.windows.net --runs <runs folder>
python scripts/batch/sync_results_sql.py --server <server>.database.windows.net --report
```

`--dry-run` counts what a sync would write without connecting. Cases already stored are
skipped unless `--refresh`; graded cases are not graded again unless `--regrade` (for
instance after a scorer change). Sign-in is Microsoft Entra only.

The control function archives cluster cases every six hours, and on `POST /api/archive`,
connecting only when the results share holds cases it has not archived yet, so an idle day
costs no database time.

Held-out results must not be loaded before the protocol is signed
([v1 held-out protocol](specs/v1-heldout-protocol.md)): keep held-out run folders out of the
`--runs` tree until then.

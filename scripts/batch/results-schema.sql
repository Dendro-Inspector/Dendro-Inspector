-- Results database (Azure SQL): every case a run stored, the labels units were scored
-- against, and the grades. Idempotent: safe to apply again. Batches are separated by GO
-- lines; scripts/batch/sync_results_sql.py --apply-schema runs them in order.
--
-- Rows come from scripts/batch/results_rows.py (cases, decisions) and
-- scripts/batch/sync_results_sql.py (labels, units, grades). The full record is kept in
-- cases.record, so anything not broken out into a column can still be read with OPENJSON
-- or JSON_VALUE, and a record can be written back to a file for replay.

IF SCHEMA_ID(N'dendro') IS NULL EXEC(N'CREATE SCHEMA dendro');
GO

-- One stored case: a cluster run's case, or a unit a local run script ran.
IF OBJECT_ID(N'dendro.cases') IS NULL
CREATE TABLE dendro.cases (
    case_key              nvarchar(300) NOT NULL CONSTRAINT pk_cases PRIMARY KEY,
    source                varchar(16)   NOT NULL,  -- cluster | local
    run_id                nvarchar(200) NOT NULL,  -- cluster run id, or local collection
    case_id               nvarchar(120) NOT NULL,
    exit_code             int           NULL,
    succeeded             bit           NOT NULL,
    code_sha              varchar(40)   NULL,
    code_dirty            bit           NULL,
    package_version       varchar(40)   NULL,
    config_sha256         varchar(64)   NULL,
    knowledge_sha256      varchar(64)   NULL,
    review_authority      varchar(40)   NULL,
    selective_arbitration bit           NULL,
    models                nvarchar(1000) NULL,     -- JSON: role -> model
    photos                int           NULL,
    subjects              int           NULL,
    target_subject_id     nvarchar(120) NULL,
    model_calls           int           NULL,
    input_tokens          bigint        NULL,
    output_tokens         bigint        NULL,
    cached_input_tokens   bigint        NULL,
    reported_cost_usd     decimal(12,4) NULL,      -- only where the provider reports it
    wall_seconds          float         NULL,
    started_at            datetime2(0)  NULL,      -- UTC
    archived_at           datetime2(0)  NOT NULL CONSTRAINT df_cases_archived DEFAULT SYSUTCDATETIME(),
    record                nvarchar(max) NULL,      -- the whole stored record, JSON
    CONSTRAINT ck_cases_source CHECK (source IN ('cluster', 'local')),
    CONSTRAINT ck_cases_record CHECK (record IS NULL OR ISJSON(record) = 1)
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_cases_run')
CREATE INDEX ix_cases_run ON dendro.cases (run_id, case_id);
GO

-- The verdict on each subject of a case.
IF OBJECT_ID(N'dendro.decisions') IS NULL
CREATE TABLE dendro.decisions (
    case_key          nvarchar(300) NOT NULL
        CONSTRAINT fk_decisions_case REFERENCES dendro.cases (case_key) ON DELETE CASCADE,
    subject_id        nvarchar(120) NOT NULL,
    is_target         bit           NULL,  -- NULL: the response designated no target
    selected_taxon    nvarchar(120) NULL,
    resolution        varchar(16)   NULL,
    confidence        varchar(16)   NULL,
    status            varchar(40)   NULL,
    visual_hypothesis nvarchar(120) NULL,
    best_next_photo   nvarchar(120) NULL,
    CONSTRAINT pk_decisions PRIMARY KEY (case_key, subject_id)
);
GO

-- Reference labels, by label set (one labels file) and unit. genus NULL: the reference
-- cannot establish it.
IF OBJECT_ID(N'dendro.labels') IS NULL
CREATE TABLE dendro.labels (
    label_set          nvarchar(160) NOT NULL,
    unit_id            nvarchar(120) NOT NULL,
    genus              varchar(60)   NULL,
    family             varchar(60)   NULL,
    target_subject_ids nvarchar(1000) NULL,  -- JSON array, or "all"
    label_visible      bit           NULL,
    CONSTRAINT pk_labels PRIMARY KEY (label_set, unit_id)
);
GO

-- Which case ran which unit in which collection (a run folder's ledger or cases folder).
IF OBJECT_ID(N'dendro.units') IS NULL
CREATE TABLE dendro.units (
    collection nvarchar(200) NOT NULL,
    unit_id    nvarchar(120) NOT NULL,
    case_key   nvarchar(300) NOT NULL
        CONSTRAINT fk_units_case REFERENCES dendro.cases (case_key) ON DELETE CASCADE,
    arm        nvarchar(80)  NULL,   -- the collection's last folder: foundry, vibe, anthropic...
    label_set  nvarchar(160) NULL,
    CONSTRAINT pk_units PRIMARY KEY (collection, unit_id)
);
GO

-- The engine's grade (dendro_inspector.evaluation.label_scoring) of a case against a label.
IF OBJECT_ID(N'dendro.grades') IS NULL
CREATE TABLE dendro.grades (
    case_key  nvarchar(300) NOT NULL
        CONSTRAINT fk_grades_case REFERENCES dendro.cases (case_key) ON DELETE CASCADE,
    label_set nvarchar(160) NOT NULL,
    unit_id   nvarchar(120) NOT NULL,
    outcome   varchar(40)   NOT NULL,
    scope     varchar(40)   NOT NULL,
    scorer    varchar(40)   NOT NULL,  -- dendro-inspector version that graded
    graded_at datetime2(0)  NOT NULL CONSTRAINT df_grades_graded DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_grades PRIMARY KEY (case_key, label_set)
);
GO

-- One row per unit of a collection: its label, grade and the target subject's verdict.
CREATE OR ALTER VIEW dendro.unit_results AS
SELECT u.collection, u.arm, u.unit_id, u.label_set, l.genus AS label_genus,
       g.outcome, g.scope, c.case_key, c.source, c.run_id, c.code_sha, c.models,
       c.review_authority, d.selected_taxon, d.resolution, d.confidence,
       d.best_next_photo, c.input_tokens, c.output_tokens, c.reported_cost_usd,
       c.wall_seconds, c.started_at
FROM dendro.units AS u
JOIN dendro.cases AS c ON c.case_key = u.case_key
LEFT JOIN dendro.labels AS l ON l.label_set = u.label_set AND l.unit_id = u.unit_id
LEFT JOIN dendro.grades AS g ON g.case_key = u.case_key AND g.label_set = u.label_set
OUTER APPLY (
    SELECT TOP (1) x.selected_taxon, x.resolution, x.confidence, x.best_next_photo
    FROM dendro.decisions AS x
    WHERE x.case_key = c.case_key
    ORDER BY CASE WHEN x.is_target = 1 THEN 0 ELSE 1 END, x.subject_id
) AS d;
GO

-- Outcomes per collection, with the wrong-genus rate among single-genus names and the
-- upper end of its 95% Wilson interval (the planning target is under 5%).
CREATE OR ALTER VIEW dendro.collection_summary AS
WITH t AS (
    SELECT collection, arm,
           COUNT(*) AS units,
           SUM(CASE WHEN outcome = 'correct' THEN 1 ELSE 0 END) AS correct,
           SUM(CASE WHEN outcome = 'wrong' THEN 1 ELSE 0 END) AS wrong,
           SUM(CASE WHEN outcome IN ('broader_only', 'unverified_broader') THEN 1 ELSE 0 END)
               AS broader_only,
           SUM(CASE WHEN outcome = 'no_name' THEN 1 ELSE 0 END) AS no_name,
           SUM(CASE WHEN outcome IS NULL THEN 1 ELSE 0 END) AS ungraded,
           SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)) AS tokens,
           SUM(reported_cost_usd) AS reported_cost_usd,
           MIN(started_at) AS first_started,
           MAX(started_at) AS last_started
    FROM dendro.unit_results
    GROUP BY collection, arm
)
SELECT t.*,
       t.units - t.correct - t.wrong - t.broader_only - t.no_name - t.ungraded AS other,
       t.correct + t.wrong AS named,
       CASE WHEN n.n > 0 THEN CAST(t.wrong AS float) / n.n END AS wrong_rate,
       CASE WHEN n.n > 0 THEN
           (n.p + 3.8416 / (2 * n.n)
            + 1.96 * SQRT(n.p * (1 - n.p) / n.n + 3.8416 / (4 * n.n * n.n)))
           / (1 + 3.8416 / n.n)
       END AS wrong_rate_upper_95
FROM t
CROSS APPLY (
    SELECT CAST(t.correct + t.wrong AS float) AS n,
           CASE WHEN t.correct + t.wrong > 0
                THEN CAST(t.wrong AS float) / (t.correct + t.wrong) ELSE 0 END AS p
) AS n;
GO

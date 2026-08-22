<#
.SYNOPSIS
    Send Paper's telemetry events to BigQuery so they outlive Cloud Logging.

.DESCRIPTION
    The API writes paper.telemetry.v1 events to stdout, which Cloud Run turns
    into structured Cloud Logging entries. Those entries are kept for 30 days
    and then deleted, so questions that need a longer window - does anyone come
    back after a month, is the failure rate improving - can never be answered
    from logs alone.

    This creates a one-way export: matching log entries are copied into a
    BigQuery dataset as they arrive, where they can be queried with SQL and
    kept for as long as you want. It changes nothing about the API, adds no
    dependency, and cannot fail a read.

    Safe to run more than once. Anything that already exists is left alone.

.PARAMETER Project
    The Google Cloud project holding the Cloud Run service.

.PARAMETER Verify
    Report what is already set up and change nothing.

.EXAMPLE
    .\setup_telemetry_export.ps1

.EXAMPLE
    .\setup_telemetry_export.ps1 -Verify
#>

[CmdletBinding()]
param(
    [string]$Project = "prj-id-misc",
    [string]$Dataset = "paper_telemetry",
    [string]$Region = "asia-south1",
    [string]$SinkName = "paper-telemetry",
    [switch]$Verify
)

$ErrorActionPreference = "Stop"

# Only Paper's own events. Uvicorn's request lines and every other container
# log stay in Cloud Logging, where they belong, and out of the dataset.
$LogFilter = 'jsonPayload.schema="paper.telemetry.v1"'
$Destination = "bigquery.googleapis.com/projects/$Project/datasets/$Dataset"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Done {
    param([string]$Message)
    Write-Host "    $Message" -ForegroundColor Green
}

function Write-Skip {
    param([string]$Message)
    Write-Host "    $Message" -ForegroundColor DarkGray
}

function Assert-Command {
    param([string]$Name, [string]$Hint)
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $found) {
        throw "'$Name' was not found on PATH. $Hint"
    }
}

function Test-LastExit {
    param([string]$What)
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed with exit code $LASTEXITCODE. The output above says why."
    }
}

# ---------------------------------------------------------------- checks

Write-Step "Checking prerequisites"

Assert-Command -Name "gcloud" -Hint "Install the Google Cloud CLI: https://cloud.google.com/sdk/docs/install"
Assert-Command -Name "bq" -Hint "It ships with the Google Cloud CLI. Try: gcloud components install bq"

$account = gcloud config get-value account 2>$null
if ([string]::IsNullOrWhiteSpace($account) -or $account -eq "(unset)") {
    throw "No active gcloud account. Run: gcloud auth login"
}
Write-Done "gcloud and bq present, signed in as $account"
Write-Done "Target project: $Project"

# ---------------------------------------------------------------- verify only

if ($Verify) {
    Write-Step "Checking what is already set up"

    $datasetsJson = bq ls --format=json --project_id=$Project 2>$null
    $hasDataset = $false
    if (-not [string]::IsNullOrWhiteSpace($datasetsJson)) {
        $datasets = $datasetsJson | ConvertFrom-Json
        foreach ($item in $datasets) {
            if ($item.datasetReference.datasetId -eq $Dataset) { $hasDataset = $true }
        }
    }
    if ($hasDataset) { Write-Done "Dataset $Dataset exists" } else { Write-Skip "Dataset $Dataset is missing" }

    $sinksJson = gcloud logging sinks list --project=$Project --format=json
    $sink = $null
    if (-not [string]::IsNullOrWhiteSpace($sinksJson)) {
        $sinks = $sinksJson | ConvertFrom-Json
        foreach ($item in $sinks) {
            if ($item.name -eq $SinkName) { $sink = $item }
        }
    }
    if ($null -eq $sink) {
        Write-Skip "Sink $SinkName is missing"
    }
    else {
        Write-Done "Sink $SinkName exists"
        Write-Host "      destination : $($sink.destination)"
        Write-Host "      filter      : $($sink.filter)"
        Write-Host "      writer       : $($sink.writerIdentity)"
    }

    Write-Host ""
    Write-Host "Nothing was changed. Run without -Verify to set up anything missing."
    return
}

# ---------------------------------------------------------------- apis

Write-Step "Enabling the APIs this needs"

gcloud services enable bigquery.googleapis.com logging.googleapis.com --project=$Project
Test-LastExit -What "Enabling APIs"
Write-Done "BigQuery and Cloud Logging APIs enabled"

# ---------------------------------------------------------------- dataset

Write-Step "Creating the BigQuery dataset"

# Listing rather than describing: a missing dataset is a normal outcome here,
# not an error worth printing.
$datasetsJson = bq ls --format=json --project_id=$Project 2>$null
$hasDataset = $false
if (-not [string]::IsNullOrWhiteSpace($datasetsJson)) {
    $datasets = $datasetsJson | ConvertFrom-Json
    foreach ($item in $datasets) {
        if ($item.datasetReference.datasetId -eq $Dataset) { $hasDataset = $true }
    }
}

if ($hasDataset) {
    Write-Skip "Dataset $Dataset already exists, leaving it alone"
}
else {
    # Same region as the Cloud Run service, so the export stays in one place.
    bq --location=$Region mk --dataset --description="Paper reader telemetry" "${Project}:${Dataset}"
    Test-LastExit -What "Creating dataset $Dataset"
    Write-Done "Created dataset $Dataset in $Region"
}

# ---------------------------------------------------------------- sink

Write-Step "Creating the log sink"

$sinksJson = gcloud logging sinks list --project=$Project --format=json
Test-LastExit -What "Listing log sinks"
$existingSink = $null
if (-not [string]::IsNullOrWhiteSpace($sinksJson)) {
    $sinks = $sinksJson | ConvertFrom-Json
    foreach ($item in $sinks) {
        if ($item.name -eq $SinkName) { $existingSink = $item }
    }
}

if ($null -eq $existingSink) {
    # Partitioned tables keep queries cheap: BigQuery only scans the days asked
    # for instead of every event ever exported.
    gcloud logging sinks create $SinkName $Destination `
        --project=$Project `
        --use-partitioned-tables `
        --log-filter=$LogFilter
    Test-LastExit -What "Creating sink $SinkName"
    Write-Done "Created sink $SinkName"
}
else {
    Write-Skip "Sink $SinkName already exists, updating its filter and destination"
    gcloud logging sinks update $SinkName $Destination `
        --project=$Project `
        --log-filter=$LogFilter
    Test-LastExit -What "Updating sink $SinkName"
    Write-Done "Sink $SinkName is up to date"
}

# ---------------------------------------------------------------- permission

Write-Step "Granting the sink permission to write"

# The sink writes as its own service account, which starts with no access to
# the dataset. Without this the sink exists but silently exports nothing.
$writerIdentity = gcloud logging sinks describe $SinkName --project=$Project --format='value(writerIdentity)'
Test-LastExit -What "Reading the sink's writer identity"

if ([string]::IsNullOrWhiteSpace($writerIdentity)) {
    throw "The sink has no writer identity yet. Wait a moment and run this again."
}

Write-Host "    writer: $writerIdentity"
gcloud projects add-iam-policy-binding $Project `
    --member=$writerIdentity `
    --role=roles/bigquery.dataEditor `
    --condition=None `
    --quiet | Out-Null
Test-LastExit -What "Granting bigquery.dataEditor"
Write-Done "Granted bigquery.dataEditor on $Project"

# ---------------------------------------------------------------- view

$table = "$Project.$Dataset.run_googleapis_com_stdout"

Write-Step "Creating the flattened events view"

# The sink's table is log-shaped: every field nested under jsonPayload, beside
# a dozen Cloud Logging columns, with every number stored as a float because
# Cloud Logging holds JSON as a protobuf struct. The view is what makes it read
# like an events table, so day-to-day queries never touch the raw shape.

# A column exists in the sink's table only once a field has actually been sent,
# and which fields appear depends on what has happened so far - a failure
# carries no block count, a success carries no reason. Fields that have not
# arrived yet are selected as typed NULLs, so the view has the same columns
# from the first day and no query has to be rewritten later.
$viewColumns = @(
    @{ Path = "timestamp";                          Alias = "event_time";        Type = "TIMESTAMP" }
    @{ Path = "resource.labels.revision_name";      Alias = "revision";          Type = "STRING" }
    @{ Path = "jsonPayload.event";                  Alias = "event";             Type = "STRING" }
    @{ Path = "jsonPayload.device_id";              Alias = "device_id";         Type = "STRING" }
    @{ Path = "jsonPayload.read_id";                Alias = "read_id";           Type = "STRING" }
    @{ Path = "jsonPayload.source_url";             Alias = "source_url";        Type = "STRING" }
    @{ Path = "jsonPayload.source_host";            Alias = "source_host";       Type = "STRING" }
    @{ Path = "jsonPayload.source_type";            Alias = "source_type";       Type = "STRING" }
    @{ Path = "jsonPayload.title";                  Alias = "title";             Type = "STRING" }
    @{ Path = "jsonPayload.origin";                 Alias = "origin";            Type = "STRING" }
    @{ Path = "jsonPayload.stage";                  Alias = "stage";             Type = "STRING" }
    @{ Path = "jsonPayload.reason";                 Alias = "reason";            Type = "STRING" }
    @{ Path = "jsonPayload.status_code";            Alias = "status_code";       Type = "INT64" }
    @{ Path = "jsonPayload.elapsed_ms";             Alias = "elapsed_ms";        Type = "INT64" }
    @{ Path = "jsonPayload.source_bytes";           Alias = "source_bytes";      Type = "INT64" }
    @{ Path = "jsonPayload.block_count";            Alias = "block_count";       Type = "INT64" }
    @{ Path = "jsonPayload.page_count";             Alias = "page_count";        Type = "INT64" }
    @{ Path = "jsonPayload.chunk_count";            Alias = "chunk_count";       Type = "INT64" }
    @{ Path = "jsonPayload.retry_count";            Alias = "retry_count";       Type = "INT64" }
    @{ Path = "jsonPayload.prompt_tokens";          Alias = "prompt_tokens";     Type = "INT64" }
    @{ Path = "jsonPayload.completion_tokens";      Alias = "completion_tokens"; Type = "INT64" }
    @{ Path = "jsonPayload.tokens_measured";        Alias = "tokens_measured";   Type = "BOOL" }
    @{ Path = "jsonPayload.percent";                Alias = "percent";           Type = "INT64" }
    @{ Path = "jsonPayload.final";                  Alias = "is_final";          Type = "BOOL" }
    @{ Path = "jsonPayload.repeat";                 Alias = "is_repeat";         Type = "BOOL" }
    @{ Path = "jsonPayload.stage_ms.fetching";      Alias = "ms_fetching";       Type = "INT64" }
    @{ Path = "jsonPayload.stage_ms.downloading";   Alias = "ms_downloading";    Type = "INT64" }
    @{ Path = "jsonPayload.stage_ms.checking";      Alias = "ms_checking";       Type = "INT64" }
    @{ Path = "jsonPayload.stage_ms.extracting";    Alias = "ms_extracting";     Type = "INT64" }
    @{ Path = "jsonPayload.stage_ms.structuring";   Alias = "ms_structuring";    Type = "INT64" }
    @{ Path = "jsonPayload.stage_ms.validating";    Alias = "ms_validating";     Type = "INT64" }
)

function Get-SchemaPaths {
    param($Fields, [string]$Prefix = "")
    $paths = @()
    foreach ($field in $Fields) {
        if ($Prefix -eq "") { $path = $field.name } else { $path = "$Prefix.$($field.name)" }
        $paths += $path
        if ($null -ne $field.fields) {
            $paths += Get-SchemaPaths -Fields $field.fields -Prefix $path
        }
    }
    return $paths
}

# The sink creates the table when the first matching event arrives, not when
# the sink is created. Before that there is nothing to build a view on.
$tablesJson = bq ls --format=json "${Project}:${Dataset}" 2>$null
$hasTable = $false
if (-not [string]::IsNullOrWhiteSpace($tablesJson)) {
    $tables = $tablesJson | ConvertFrom-Json
    foreach ($item in $tables) {
        if ($item.tableReference.tableId -eq "run_googleapis_com_stdout") { $hasTable = $true }
    }
}

if (-not $hasTable) {
    Write-Skip "No events have arrived yet, so there is no table to build the view on."
    Write-Skip "Open a document on the live site, wait a minute, then run this script again."
}
else {
    $schemaJson = bq show --schema --format=json "${Project}:${Dataset}.run_googleapis_com_stdout"
    Test-LastExit -What "Reading the exported table's schema"
    $presentPaths = Get-SchemaPaths -Fields ($schemaJson | ConvertFrom-Json)

    $selected = @()
    $missing = @()
    foreach ($column in $viewColumns) {
        if ($presentPaths -contains $column.Path) {
            if ($column.Type -eq "INT64") {
                $selected += "CAST(t.$($column.Path) AS INT64) AS $($column.Alias)"
            }
            else {
                $selected += "t.$($column.Path) AS $($column.Alias)"
            }
        }
        else {
            $selected += "CAST(NULL AS $($column.Type)) AS $($column.Alias)"
            $missing += $column.Alias
        }
    }

    # Built from a literal template so PowerShell never treats a SQL backtick as
    # an escape. BigQuery needs them: the project id contains a hyphen.
    $viewTemplate = @'
CREATE OR REPLACE VIEW `__VIEW__` AS
SELECT
  __COLUMNS__
FROM `__TABLE__` AS t
WHERE t.jsonPayload.schema = 'paper.telemetry.v1'
'@
    $viewSql = $viewTemplate.Replace("__VIEW__", "$Project.$Dataset.events")
    $viewSql = $viewSql.Replace("__COLUMNS__", ($selected -join ",`n  "))
    $viewSql = $viewSql.Replace("__TABLE__", $table)

    # Kept beside the script so the generated SQL is reviewable, and runnable by
    # hand if this ever needs adjusting.
    $sqlPath = Join-Path $PSScriptRoot "output\telemetry_events_view.sql"
    New-Item -ItemType Directory -Force -Path (Split-Path $sqlPath) | Out-Null
    Set-Content -Path $sqlPath -Value $viewSql -Encoding utf8

    # Passed as one line: a multi-line argument to a native command is not
    # reliably quoted on Windows.
    $oneLine = ($viewSql -replace '\s+', ' ').Trim()
    bq query --use_legacy_sql=false --project_id=$Project --quiet $oneLine | Out-Null
    Test-LastExit -What "Creating the events view"

    Write-Done "Created view $Dataset.events with $($selected.Count) columns"
    Write-Done "SQL saved to $sqlPath"
    if ($missing.Count -gt 0) {
        Write-Skip "Selected as NULL until such an event happens: $($missing -join ', ')"
        Write-Skip "Re-run this script after more traffic to bind them to real columns."
    }
}

# ---------------------------------------------------------------- done

$view = "$Project.$Dataset.events"

Write-Host ""
Write-Host "Telemetry export is set up." -ForegroundColor Green
Write-Host ""
Write-Host "Query the events view, not the raw table underneath it:"
Write-Host "https://console.cloud.google.com/bigquery?project=$Project"

$queryTemplate = @'

-- What people are reading
SELECT source_host, COUNT(*) AS reads
FROM `__VIEW__`
WHERE event = 'read_prepared'
GROUP BY 1
ORDER BY reads DESC;

-- How the pipeline is breaking
SELECT reason, stage, COUNT(*) AS failures
FROM `__VIEW__`
WHERE event IN ('read_failed', 'read_rejected')
GROUP BY 1, 2
ORDER BY failures DESC;

-- Where the time goes on a finished read
SELECT
  COUNT(*) AS reads,
  APPROX_QUANTILES(elapsed_ms, 100)[OFFSET(50)] AS median_ms,
  APPROX_QUANTILES(elapsed_ms, 100)[OFFSET(90)] AS p90_ms,
  ROUND(AVG(ms_downloading)) AS avg_downloading_ms,
  ROUND(AVG(ms_checking)) AS avg_checking_ms,
  ROUND(AVG(ms_structuring)) AS avg_structuring_ms,
  SUM(retry_count) AS chunk_retries
FROM `__VIEW__`
WHERE event = 'read_prepared';

-- New browsers versus browsers that came back
WITH first_seen AS (
  SELECT device_id, MIN(DATE(event_time)) AS first_day
  FROM `__VIEW__`
  WHERE device_id IS NOT NULL
  GROUP BY 1
)
SELECT
  DATE(e.event_time) AS day,
  COUNTIF(f.first_day = DATE(e.event_time)) AS new_browsers,
  COUNTIF(f.first_day < DATE(e.event_time)) AS returning_browsers
FROM `__VIEW__` e
JOIN first_seen f USING (device_id)
WHERE e.event = 'read_attempted'
GROUP BY day
ORDER BY day;

-- How far people actually get, not just what they opened
SELECT percent, COUNT(*) AS sessions
FROM `__VIEW__`
WHERE event = 'reading_progress' AND is_final
GROUP BY 1
ORDER BY 1;

-- What one read costs, at the current DeepSeek rate
SELECT
  ROUND(SUM(prompt_tokens) / 1e6 * 0.06006 + SUM(completion_tokens) / 1e6 * 0.12012, 4) AS usd_total,
  ROUND(AVG(prompt_tokens) / 1e6 * 0.06006 + AVG(completion_tokens) / 1e6 * 0.12012, 5) AS usd_per_read
FROM `__VIEW__`
WHERE event = 'read_prepared';
'@

Write-Host $queryTemplate.Replace("__VIEW__", $view)

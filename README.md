# Paper

Just read — a better reading experience for anything on the public internet.

Paper turns public documents into a calm, comfortable reading view. It starts with public PDFs and is growing toward books, essays, papers, and long-form web pages.

Try Paper: [mukesh1811.github.io/paper](https://mukesh1811.github.io/paper/)

Backend architecture: [URL2JSON](api/URL2JSON_IMPLEMENTATION_PLAN.md)

## Telemetry

The Cloud Run API emits structured `paper.telemetry.v1` JSON logs for each attempted, rejected, prepared, browser-opened, and read public source URL. `local_run.bat` also writes them to `output/telemetry/events.jsonl` and exposes a local viewer at [127.0.0.1:8000/telemetry](http://127.0.0.1:8000/telemetry).

Each event answers one of three questions:

- **What people read.** The source URL and host, its type and size, and how the reader arrived at it (`pasted`, `sample`, `link`, `reload`).
- **How the pipeline behaves.** `stage_ms` per stage, the failure `reason` and the stage it reached, and for structuring the chunk count, retry count, and provider token usage.
- **Whether people come back.** A `device_id` the browser generates for itself and keeps beside its reading copies, plus `cache_hit` and `reading_progress` milestones so an opened document can be told from a finished one.

Paper records no account, IP address, or document text. Source URLs are recorded because they are the compatibility signal. The `device_id` identifies storage rather than a person: clearing site data takes the reading copies and the identifier together, and the browser is then a new one.

### Reading the events in production

Live events are in [Logs Explorer](https://console.cloud.google.com/logs/query?project=prj-id-misc), filtered with `jsonPayload.schema="paper.telemetry.v1"`.

Cloud Logging keeps them for 30 days, which is too short to answer whether anyone comes back. `setup_telemetry_export.ps1` exports them to BigQuery, where they can be kept and queried with SQL. Run it once, before it matters:

```powershell
.\setup_telemetry_export.ps1 -Verify   # report what exists, change nothing
.\setup_telemetry_export.ps1           # create anything missing
```

The sink's own table is log-shaped: fields nested under `jsonPayload`, beside a dozen Cloud Logging columns, with every number stored as a float. The script also builds a `paper_telemetry.events` view that flattens it into ordinary typed columns, so queries read like an events table. Query the view; the table underneath it is plumbing.

The view keeps the same columns from the first day. A field that has not been sent yet has no column in the exported table, so it is selected as a typed `NULL` until it appears — re-run the script after more traffic to bind it to the real column.

It finishes by printing the queries for what people read, how the pipeline breaks, where the time goes, new versus returning browsers, how far people actually read, and what a read costs.

# Notes

## Studio condition regression (2026-09-21)

The `etch/condition` block's `conditionString: 'this.fields.video_url'` stopped
rendering after the Studio build 2026.09.21 update. The block is emitted as a
Svelte `{#if ${conditionString}}`; the `{#if}` evaluation of
`this.fields.video_url` regressed, while the raw-html `{this.fields.video_url}`
interpolation still resolves fine.

Workaround (applied): set `conditionString: 'true'` — safe because all 125 content
pages have a video. Re-test `this.fields.video_url` after the next Studio update
and drop the workaround if it renders again.

## Deploy flakiness + es_index (2026-09-22)

`es_index` (the Studio content-index data source) was temporarily chunked into
12-item pages to work around a `Loop initialization failed` error — but that was
a transient server-load spike (from bulk REST writes during the R2 migration),
not a real problem, and the chunking made deploys slower and flakier (13
sequential calls vs 6 parallel).

Reverted to the original 6 parallel `queryContent({type, limit:-1})` calls.
Deploys now return 200 in ~25s consistently. Don't re-chunk `es_index` unless a
query is measured (unloaded) to genuinely exceed the 1000ms sandbox timeout.

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

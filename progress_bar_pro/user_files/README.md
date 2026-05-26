This folder is reserved for Progress Bar Pro user settings.

Anki preserves `user_files` during normal add-on updates. The add-on writes
`settings_backup.json` here after you save options, so your colors and toggles
can be recovered if Anki's generated config metadata is reset.

The add-on also writes `timing_history.json` here. It keeps long-term answer
times for smarter estimates, while the estimate data graph focuses on the most
recent 15 days.

The add-on writes `daily_progress.json` here to remember today's deck name,
starting card count, answered count, and mini answer-time chart. If this folder
is shared between computers, the reviewer progress bar and bubble-side chart can
resume from the same state elsewhere.

If you set a custom database folder in Options, add-on data files are stored
there and these default copies are no longer used.

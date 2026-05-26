This folder is reserved for Progress Bar Pro user settings.

Anki preserves `user_files` during normal add-on updates. The add-on writes
`settings_backup.json` here after you save options, so your colors and toggles
can be recovered if Anki's generated config metadata is reset.

The add-on also writes `timing_history.json` here. It keeps long-term answer
times for smarter estimates, while the estimate data graph focuses on the most
recent 15 days.

If you set a custom database folder in Options, `timing_history.json` is moved
there and this default copy is no longer used.

# Progress Bar Pro

An Anki 25.09-compatible reviewer progress bar add-on.

## Install

In Anki desktop, use `Tools -> Add-ons -> Install from file...` and select `progress_bar_pro.ankiaddon` from this folder.

## Options

Open `Tools -> Progress bar pro` to set:

- Filled bar color
- Unfilled background color
- Bubble background and text colors
- Answer time chart Good/Again gradient colors
- Estimate data chart gradient colors
- Again answer chart gradient colors
- Center bubble display preset
- Optional answer time in the bubble
- Optional mini chart for the last 5 answer times
- Optional always-visible bubble/chart display
- Optional estimated time remaining line
- Top or bottom placement

Settings are saved through Anki's add-on config system. Progress Bar Pro also
writes a backup copy to `user_files/settings_backup.json`, which Anki preserves
during normal add-on updates.

Timing history is stored in `user_files/timing_history.json`. Recent and
all-time history help the estimated time remaining improve across decks and
sessions. You can set a custom database folder in Options; the add-on will use
`timing_history.json` in that folder so it can be synced by another app.

Use `Tools -> Progress Bar Pro -> View estimate data` to see the 15-day
smoothed density area graph, all-time estimate summary, and export the raw
timing history as a CSV for Google Sheets. It also highlights your best recent
day by lowest average answer time. Options can export/import the estimate
history JSON database.

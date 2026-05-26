# Progress Bar Pro

Options can be changed from `Tools -> Progress bar pro`.

- `bar_color`: filled progress color. The add-on automatically creates a lighter-to-selected-color gradient.
- `background_color`: unfilled progress track color.
- `bubble_color`: center bubble background color.
- `bubble_text_color`: center bubble text color.
- `bubble_text`: saved display preset for the center bubble.
- `bubble_duration_ms`: how long the bubble appears after you answer a card.
- `show_answer_time`: also show how long the last card took to answer.
- `show_answer_time_chart`: show a mini area chart of the last 5 answer times to the right of the bubble.
- `always_show_bubble`: keep the bubble and answer time chart visible instead of animating them after each answer.
- `show_estimated_time`: show an estimated time remaining line in the bubble.
- `show_finish_time`: show the estimated clock time when the current deck/session ends.
- `chart_good_gradient_top`: top color used for Good/Hard/Easy answers in the mini answer chart.
- `chart_good_gradient_bottom`: bottom color used for Good/Hard/Easy answers in the mini answer chart.
- `chart_gradient_top`: top color for the estimate data area graph.
- `chart_gradient_bottom`: bottom color for the estimate data area graph.
- `chart_again_gradient_top`: top color used when the mini answer chart fades after an Again answer.
- `chart_again_gradient_bottom`: bottom color used when the mini answer chart fades after an Again answer.
- `database_location`: optional folder where `timing_history.json` is stored.
- `position`: `top` or `bottom`.

Anki stores changed options in its generated add-on metadata. Progress Bar Pro
also mirrors saved options to `user_files/settings_backup.json` for normal
add-on updates.

Estimate history can be viewed from `Tools -> Progress Bar Pro -> View estimate
data` and exported as CSV.

# Changelog

What changes in behaviour, compatibility and features, release by release. Bug fixes are not
listed one by one (read `git log` or the issues); security fixes are, under "Security", with
their issue.

## 1.0.0 (pre-release: alfa-1, alfa-2)

The 1.0.0 release is in pre-release. This list is cumulative: it grows with each pre-release
and is final at the release.

### Compatibility

- Needs the library `python3-activitytrack` 1.0.0~alfa-2 or later, and so a server on 1.0
  (sync wire schema 3): with an older server the command says to update the server, and with
  an older app or PC it says to update.
- The data migrate by themselves to the activity model when the store is first opened; the
  to-do is now an activity in the state "Da fare".
- Commands removed: `suspend` (an activity is suspended when another one starts) and
  `complete`. In `--json` output `activity` replaces `title`, and `display` and
  `priority_label` are new.

### Features

- New commands: `absence` (absence, holiday, leave, sick leave; optional company, days or
  times) and `restore` (from the bin).
- `travel` from work asks whether the work is complete (`--completed`, `--not-completed` for
  scripts); `stop` offers the return trip (`--return-trip`, `--end`, `--end-with-return`);
  `reopen` asks to confirm (`--yes`) and can reopen travelling (`--travel`, `--at`).
- `todo ignore` and `todo delete` go to the bin, and the priority is named (0 none to 5
  emergency).
- Lists show absences, and every activity with the symbol, colour and name of its displayed
  state; `--show ignored` lists the deleted ones.

### Behaviour

- What a command moved or removed is now said: an end moved back to the start of a new
  activity, a planned segment or return trip removed. In text it goes to stderr, with
  `--json` in the `"notices"` list of the answer.
- The notices of the sync (a change that was not applied after the realignment) are printed
  once after a sync and then dismissed.
- `pause` with nothing being worked acts on the only activity running, suspended, and
  restarts it at work, as the key `p` of the terminal interface.

Various bug fixes.

Earlier versions: see the release notes of each tag.

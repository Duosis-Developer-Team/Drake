# Development map: people's portraits

Agent Visibility → Development shows every person as a small tile. By
default the tile is an arcade character the browser draws from the person's
GitHub picture. A hand-made portrait, set once by an operator, replaces it
everywhere on the map.

## The image

- Square PNG, WebP or JPEG; **512 × 512** is the size to aim for
  (64–2048 a side, width/height within 0.9–1.1, at most 512 KB).
- Head and shoulders, centred; the face about 60% of the height. The figure
  may run to the bottom edge.
- A transparent or plain, soft background. Transparent works best: the map
  puts a soft fill of the person's colour behind it, which reads in both
  themes.
- Name the file after the person's GitHub login (`eneserman.png`). The
  person key used below can differ (see `list`).

## Setting it

Inside an API pod (copy the file in first, e.g. `kubectl cp`):

```sh
python -m drake_api.dev_activity.portraits list
python -m drake_api.dev_activity.portraits set <person_key> /tmp/<login>.png
python -m drake_api.dev_activity.portraits remove <person_key>
```

`list` prints every person on the map with their logins and whether a
portrait is set. The person key is the map's: an alias person's key when an
alias groups several identities (`gencay` for `gencaycoskuntr` and
`coskungencay`), otherwise the GitHub login in lower case.

The file is recognised by its bytes, never its name, and refused with the
reason when it does not fit. Browsers keep a portrait for up to an hour, so
a replaced one shows after a reload once that hour has passed (or at once
in a private window).

## What is served

`GET /v1/agents/dev/people/{person_key}/avatar` answers with the portrait
(`X-Tracesis-Avatar: portrait`) when one is set, else the GitHub picture
(`X-Tracesis-Avatar: github`), else 204 (initials). Only people the caller
can see on the map get an answer; a portrait never reveals anyone else.

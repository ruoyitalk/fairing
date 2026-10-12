# Free AI news reader pilot

The existing Fairing daily digest remains the baseline. Compare [Miniflux's
official Docker setup](https://miniflux.app/docs/docker.html) and [FreshRSS's
official Docker setup](https://github.com/FreshRSS/FreshRSS/blob/edge/Docker/README.md)
with the same 15 public AI feeds. Export the reviewed feed list with:

```bash
python3 -m fairing.opml --output /private/local/path/ai-pilot.opml
```

The exporter reads only tracked `config/sources.yaml`. It refuses private feed
catalogs and URL query strings. Do not import `sources.local.yaml` into either
reader. Use the current official image digests, and validate the PostgreSQL data
mount for the chosen major version before starting the Compose file. FreshRSS
uses its built-in SQLite database for this pilot; configure its admin account
through the loopback-only setup page.

The Compose file requires isolated local state paths and private files for the
Miniflux database URL, database password, and admin password. Set those files
to mode 0600 outside Git. No secret value belongs in Compose, this repository,
or the comparison log. Both web ports bind to loopback. No public route,
single-sign-on bridge, mail sender, or automated Context write is part of the
pilot. Do not start the pilot until installation and host access are explicitly
authorized for the specific machine.

For seven days, record per product: expected new articles from each source,
actual arrival time, duplicates, missing original links, unread/read sync,
and the time needed to find three genuinely relevant articles. Compare against
the Fairing digest from the same source set. If a mature reader covers the
daily discovery task, keep it and reduce Fairing to any proven source filtering
gap; if both miss important announcements, document the missed source and fix
that source first. Stop either pilot if it requires a paid feature, exposes a
private feed, or stores data outside the local state directory.

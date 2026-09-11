# OPDS

Paperstand serves an [OPDS 1.2](https://specs.opds.io/opds-1.2) catalogue at `/opds`, so
that a reading app on a phone or an e-reader can browse the same titles the web interface
shows and download the issues.

The catalogue URL is:

```
http://<host>:8080/opds
```

That is the only address to type into a client. Everything else — the shelves, the covers,
the search box — the client discovers from the feed.

## The feed tree

| Feed | Kind | What is in it |
| --- | --- | --- |
| `/opds` | navigation | Today · Newspapers · Magazines · Recently added · Unsorted |
| `/opds/today` | acquisition | The day's newspapers, then the latest issue of every magazine |
| `/opds/kind/newspaper` | navigation | One entry per newspaper title |
| `/opds/kind/magazine` | navigation | One entry per magazine title |
| `/opds/titles/{id}?page=N` | acquisition | One title's issues, newest first, 50 to a page |
| `/opds/recent` | acquisition | The last 50 issues to join the catalogue |
| `/opds/unsorted?page=N` | acquisition | Files the parser could not place, 50 to a page |
| `/opds/search?q=…&page=N` | acquisition | Issues matching a title, a file name or a derived title |
| `/opds/opensearch.xml` | — | The OpenSearch description a client builds its search box from |

*Unsorted* only appears on the front page when there is something in it. Everything else is
always there, empty or not.

A few details worth knowing:

- **Duplicates never appear.** Two copies of the same issue are one entry, exactly as in the
  web interface.
- **Today falls back.** Before the morning's files arrive, the *Today* feed shows the most
  recent day that has newspapers, and its subtitle says which day that is.
- **Search is a substring match** on the title's name, the file name and the title the
  parser derived — case-insensitively for ASCII, so `almanacco` finds *L'Almanacco* but
  `citta` does not find *Città*. Fifty results to a page.
- **Three feeds are paged**, fifty issues at a time: a title's issues, *Unsorted* and a
  search. `?page=N` asks for one page directly, and a feed that has more than one carries
  the `first`, `last`, `previous` and `next` links a client follows on its own.
- **A declared publication's language becomes `dc:language`.** A title whose
  [`publication.yml`](folder-layout.md#declared-publications) sets `language` carries it on
  its own entry and on every one of its issues; a title with no declared language, or no
  `publication.yml` at all, carries no `dc:language` element.
- **The entry id follows the file's content**, not its path (see
  [Identity](folder-layout.md#identity)). A file renamed or moved anywhere else in its
  library keeps its `urn:paperstand:issue:` — a client never sees it as a new entry — while a
  copy re-downloaded with different bytes is a new issue with a new one.

Each issue entry carries its cover, its thumbnail and one acquisition link to the PDF:

```xml
<entry>
  <id>urn:paperstand:issue:5e6b9e5fa985d8c6</id>
  <title>Corriere del Ponte — 17 March 2026</title>
  <updated>2026-03-17T05:12:44+00:00</updated>
  <author><name>Corriere del Ponte</name></author>
  <dc:identifier>urn:paperstand:issue:5e6b9e5fa985d8c6</dc:identifier>
  <dc:date>2026-03-17</dc:date>
  <dc:language>it</dc:language>
  <category term="newspaper" label="Newspaper"/>
  <summary type="text">Corriere del Ponte · 17 March 2026 · 48 pages · 41.8 MB</summary>
  <link rel="http://opds-spec.org/image" href="…/cover.jpg?v=…" type="image/jpeg"/>
  <link rel="http://opds-spec.org/image/thumbnail" href="…/thumb.jpg?v=…" type="image/jpeg"/>
  <link rel="http://opds-spec.org/acquisition" href="…/file" type="application/pdf"
        title="Download PDF"/>
</entry>
```

`<dc:language>` appears here because Corriere del Ponte declares `language: it`; most titles
declare none, and their entries simply have no such element.

Reading in the app therefore means downloading the PDF. Page streaming — the OPDS-PSE
extension, which would let a client fetch one rendered page at a time from Paperstand — is
not there yet, although the endpoint behind it already exists.

## Adding the catalogue in a client

There is **no authentication**; see [Putting it behind a
password](#putting-it-behind-a-password). Leave the user name and password fields empty
everywhere, unless a reverse proxy in front of Paperstand is asking for them.

### iOS — Panels

1. *Library* → *Add* → **Add OPDS Catalog**.
2. *Address*: `http://<host>:8080/opds`. Leave *Username* and *Password* empty.
3. *Save*. The catalogue appears with its sections; an issue downloads with a tap and opens
   in the built-in reader.

### iOS — Chunky Comic Reader

1. The cog → **Add New Server** → *OPDS*.
2. *Name*: Paperstand. *URL*: `http://<host>:8080/opds`.
3. *Save*, then open the server from the shelf.

### Android — Moon+ Reader

1. *My Books* → *Net Library* → *+* → **Add new OPDS catalog**.
2. *Title*: Paperstand. *URL*: `http://<host>:8080/opds`.
3. *OK*, then open it from the catalogue list. Its search box uses the OpenSearch
   description.

### Android and e-ink — KOReader

1. The magnifier → **OPDS catalog** → *+* (top right).
2. *Title*: Paperstand. *Path*: `http://<host>:8080/opds`.
3. Long-press the entry to edit it later. Downloads land in KOReader's download folder.

### Android — Librera Reader

1. *Cloud* → *OPDS* → *+*.
2. *Name*: Paperstand. *URL*: `http://<host>:8080/opds`.
3. *Add*, then browse.

If a client shows the catalogue but no covers, the URLs it is following are not reachable
from the phone. That is a base URL problem, and the next section is the fix.

## Absolute URLs, base URL and reverse proxies

Every link in the feed is absolute, because a client stores the catalogue and comes back to
it days later from a different network. Paperstand builds those URLs in one of two ways.

**From `PAPERSTAND_BASE_URL`, when it is set.** This wins over everything, and is what to
reach for whenever the address the outside world uses is not something the container can
work out — a sub-path, a different port, a domain that only exists in somebody's DNS:

```yaml
environment:
  PAPERSTAND_BASE_URL: https://paperstand.example.org
```

**From the request, when it is not.** The scheme and the host the request arrived with,
corrected by the reverse proxy's `X-Forwarded-Proto` and `X-Forwarded-Host` headers — but
only when the request comes from an address listed in `PAPERSTAND_TRUSTED_PROXIES`:

```yaml
environment:
  # The proxy's address, a CIDR block, a comma-separated list of either, or `*`
  # when the published port is one only the proxy can reach.
  PAPERSTAND_TRUSTED_PROXIES: 172.18.0.0/16
```

The default is `127.0.0.1`, which is right when the port is reached directly. Anyone who can
reach the port can *send* those headers; only the clients named here are believed, because a
forged host is a catalogue full of links pointing at somebody else's server.

> **Start the server with `paperstand serve`.** The container does, and `make dev-api` does
> the equivalent. Paperstand reads the forwarded headers itself, and Uvicorn reads them too
> unless it is told not to — its middleware runs *outside* the application and rewrites the
> client address from `X-Forwarded-For` before Paperstand can decide whether the request
> came from a trusted proxy. Paperstand then sees the end user's address rather than the
> proxy's, declines to believe `X-Forwarded-Host`, and the feed comes out full of backend
> URLs. If you start Uvicorn yourself, pass `--no-proxy-headers`:
>
> ```bash
> uvicorn paperstand.main:app --no-proxy-headers --port 8080
> ```

## Putting it behind a password

Paperstand has no users and no authentication. Anything reachable from outside the house
belongs behind a reverse proxy that asks for a password — and the password has to cover the
**whole application**, not only `/opds`: the feed's acquisition links are `/api/…` URLs, and
protecting the catalogue while leaving the PDFs open protects nothing.

Both snippets assume Paperstand is listening on `paperstand:8080`.

### Caddy

```caddyfile
paperstand.example.org {
    # caddy hash-password --plaintext 'the password'
    basic_auth {
        reader $2a$14$Zt1Q0Ry2ZQ0Q0y3s7yWQfeJ0m2sJ3wZ6h1O9r4YyR5xR0v2S8k3Wm
    }

    reverse_proxy paperstand:8080
}
```

Caddy terminates TLS itself and sets the forwarded headers on its own, so the only thing
Paperstand needs is `PAPERSTAND_TRUSTED_PROXIES` naming the Caddy container's address.

### nginx

```nginx
server {
    listen 443 ssl;
    server_name paperstand.example.org;

    ssl_certificate     /etc/ssl/paperstand.crt;
    ssl_certificate_key /etc/ssl/paperstand.key;

    location / {
        # htpasswd -c /etc/nginx/paperstand.htpasswd reader
        auth_basic           "Paperstand";
        auth_basic_user_file /etc/nginx/paperstand.htpasswd;

        proxy_pass http://paperstand:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;

        # A newspaper is a large PDF and a reader asks for byte ranges.
        proxy_buffering off;
    }
}
```

Every client listed above supports HTTP basic authentication: put the user name and the
password into the fields the catalogue dialog offers, and use the `https://` address.

## Checking it by hand

```bash
curl -s localhost:8080/opds | head -40
curl -sI localhost:8080/opds | grep -i content-type
curl -s "localhost:8080/opds/titles/<id>?page=2" | head -40
curl -s -H 'X-Forwarded-Proto: https' -H 'X-Forwarded-Host: example.org' \
  localhost:8080/opds | grep -o 'https://example.org[^"]*' | head
```

The last one only rewrites the URLs when the address `curl` is calling from is trusted —
from the host itself that is `127.0.0.1`, which is the default.

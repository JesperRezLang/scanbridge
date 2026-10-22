# Destinations

A destination is a place where scanbridge stores a finished scan. There are five types: [`local`](#local), [`seafile`](#seafile), [`webdav`](#webdav), [`paperless`](#paperless) and [`ftp`](#ftp). A target (a name in the printer's "Scan to PC" list) sends to one or more destinations; see the [README](../README.md) for targets, file names and the retry queue.

## Configuring a destination

Every option can be set as an environment variable or in a TOML file. The variable name is `SCANBRIDGE_<DESTINATION>_<OPTION>`, in upper case. Setting any option of a built-in type creates a destination named after its type, so `SCANBRIDGE_WEBDAV_URL` creates a destination called `webdav` of type `webdav`.

In a TOML file a destination is a `[destinations.<name>]` table. `type` defaults to the table's name; set it to have several destinations of the same type:

```toml
[destinations.office-nas]
type = "ftp"
host = "nas.example.com"

[destinations.archive-nas]
type = "ftp"
host = "nas2.example.com"
```

Their variables use the table's name: `SCANBRIDGE_OFFICE_NAS_HOST`. Characters other than letters and digits in the name become `_`.

Notes that apply to all types:

- Options that are secrets (the `password` and `token` options below) are shown as `***` in the log and by `--check`. Like every variable they can be read from a file by appending `_FILE`, for example `SCANBRIDGE_WEBDAV_PASSWORD_FILE=/run/secrets/dav_password`.
- Boolean options take `true`/`false` (also `yes`/`no`, `on`/`off`, `1`/`0`). In TOML, write text options as text: `tags = "1,2"`, not `tags = 1`.
- Any failure to store a file (server down, wrong password, missing folder) is logged and the file goes to the retry queue, which tries again every `retry_interval` seconds.
- scanbridge never overwrites a file that already exists at the destination. It stores `name (1).ext`, `name (2).ext` and so on instead. Only Paperless works differently, see below.
- scanbridge warns at start-up when a destination would send documents and credentials unencrypted to another machine: a plain `http://` URL, or an FTP destination with `tls = false`. Addresses on the same host (`localhost`, `127.0.0.1`) are not reported.
- None of the types create folders on the remote side, except `local`.

## local

For a folder on the machine that runs scanbridge. Network shares (SMB, NFS) work when they are mounted on the host and passed into the container as a volume.

| Option | Env variable | Default | Description |
| --- | --- | --- | --- |
| `path` | `SCANBRIDGE_LOCAL_PATH` | required | Folder to save scans in. Created if missing. |

Credentials: none. The user that runs scanbridge (uid 10001 in the Docker image) needs write permission on the folder.

```toml
[destinations.local]
path = "/scans"
```

```sh
SCANBRIDGE_LOCAL_PATH=/scans
```

Name clashes: `scan.pdf` becomes `scan (1).pdf`. Files are written through a temporary `.part` file and renamed, so a folder watcher never sees half a file. Errors: a folder that cannot be created or written to makes the delivery fail and be queued.

## seafile

For a [Seafile](https://www.seafile.com/) library (Seafile 9 or later), using a library API token that only gives access to that library.

| Option | Env variable | Default | Description |
| --- | --- | --- | --- |
| `url` | `SCANBRIDGE_SEAFILE_URL` | required | Base URL of the server, for example `https://seafile.example.com`. |
| `token` | `SCANBRIDGE_SEAFILE_TOKEN` | required | Library API token. Secret. |
| `directory` | `SCANBRIDGE_SEAFILE_DIRECTORY` | `/` | Folder inside the library. It should already exist. |

Credentials: open the library in the web interface, choose Advanced → API Token and create a token with read-write permission.

```toml
[destinations.seafile]
url = "https://seafile.example.com"
token = "..."          # better: SCANBRIDGE_SEAFILE_TOKEN_FILE
directory = "/Scans"
```

```sh
SCANBRIDGE_SEAFILE_URL=https://seafile.example.com
SCANBRIDGE_SEAFILE_TOKEN_FILE=/run/secrets/seafile_token
SCANBRIDGE_SEAFILE_DIRECTORY=/Scans
```

Name clashes: Seafile renames the new file to `name (1).ext` itself. Errors: any HTTP error or connection problem fails the delivery. The upload goes to the server address you configured, even if Seafile names a different public address in its upload link.

## webdav

For anything that speaks WebDAV: Nextcloud, ownCloud, Synology (WebDAV Server package), Apache `mod_dav`, nginx with the DAV module and others.

| Option | Env variable | Default | Description |
| --- | --- | --- | --- |
| `url` | `SCANBRIDGE_WEBDAV_URL` | required | Address of the folder to upload into. The folder must exist. |
| `username` | `SCANBRIDGE_WEBDAV_USERNAME` | empty | Login name. If empty, no credentials are sent. |
| `password` | `SCANBRIDGE_WEBDAV_PASSWORD` | empty | Password or app password. Secret. |

Credentials, Nextcloud: create an app password under Settings → Security → Devices & sessions (enter a name, choose "Create new app password") and use it as `password`, with your user name as `username`. This keeps your main password out of the configuration and lets you revoke access later. The WebDAV address is shown in Files → Files settings (bottom left) as "WebDAV"; add the folder to it, for example `https://cloud.example.com/remote.php/dav/files/alice/Scans`. ownCloud shows the address in the same place. The folder has to be created in the web interface first.

```toml
[destinations.nextcloud]
type = "webdav"
url = "https://cloud.example.com/remote.php/dav/files/alice/Scans"
username = "alice"
password = "..."          # better: SCANBRIDGE_NEXTCLOUD_PASSWORD_FILE
```

```sh
SCANBRIDGE_WEBDAV_URL=https://cloud.example.com/remote.php/dav/files/alice/Scans
SCANBRIDGE_WEBDAV_USERNAME=alice
SCANBRIDGE_WEBDAV_PASSWORD_FILE=/run/secrets/webdav_password
```

How it works: scanbridge first asks with `HEAD <url>/<file name>` whether the name is taken, then sends `PUT` with Basic authentication and the header `If-None-Match: *`, which tells the server to refuse the upload if the file appeared in the meantime (`412 Precondition Failed`). For a taken name it tries `name (1).ext`, then `name (2).ext`, up to 100 names. The `HEAD` check also protects servers that ignore `If-None-Match`.

Errors: `404` or `409` is reported as "the folder probably does not exist". Wrong credentials (`401`), a full quota (`507`) and all other errors fail the delivery as well, and the file is queued. Use `https://`: Basic authentication sends the password in clear text over plain `http://`.

## paperless

For [Paperless-ngx](https://docs.paperless-ngx.com/), which reads, indexes and archives the scan. The file is posted to the Paperless API; it is then consumed in the background.

| Option | Env variable | Default | Description |
| --- | --- | --- | --- |
| `url` | `SCANBRIDGE_PAPERLESS_URL` | required | Base URL of Paperless, for example `https://paperless.example.com`. |
| `token` | `SCANBRIDGE_PAPERLESS_TOKEN` | required | API auth token. Secret. |
| `tags` | `SCANBRIDGE_PAPERLESS_TAGS` | empty | Comma-separated tag IDs to assign, for example `3,7`. |
| `correspondent` | `SCANBRIDGE_PAPERLESS_CORRESPONDENT` | empty | ID of the correspondent. |
| `document_type` | `SCANBRIDGE_PAPERLESS_DOCUMENT_TYPE` | empty | ID of the document type. |

Credentials: in Paperless open your profile (user name, top right → My Profile) and copy the API auth token, or generate one. The token has the permissions of that user, so consider a separate user that may only add documents.

IDs are the numbers Paperless uses internally. Open the tag, correspondent or document type under Manage; the number is in the address, for example `/tags/3`. They are not names. In TOML write them as text (`tags = "3,7"`).

```toml
[destinations.paperless]
url = "https://paperless.example.com"
token = "..."          # better: SCANBRIDGE_PAPERLESS_TOKEN_FILE
tags = "3,7"
correspondent = "5"
```

```sh
SCANBRIDGE_PAPERLESS_URL=https://paperless.example.com
SCANBRIDGE_PAPERLESS_TOKEN_FILE=/run/secrets/paperless_token
SCANBRIDGE_PAPERLESS_TAGS=3,7
```

How it works: scanbridge posts the file to `<url>/api/documents/post_document/` with the title set to the file name without its extension, and the tags, correspondent and document type if configured. Paperless answers with the ID of a consumption task, which is logged as `task <id>`. Whether the document then ends up in Paperless is decided by the consumer: you can follow the task in Paperless under Tasks.

Name clashes: none of scanbridge's business. Paperless compares the content of documents and refuses duplicates itself (the task then fails with a "duplicate" message in Paperless). File names are not changed.

Errors: an ID option that is not a number (`tags = "inbox"`) is a configuration error at start-up (also reported by `--check`). HTTP errors (`401` for a wrong token, `400` for a rejected request) and connection problems fail the delivery too.

## ftp

For an FTP server, with explicit FTPS (FTP over TLS, `AUTH TLS`) on by default. Typical uses are a NAS or a router with an FTP share.

| Option | Env variable | Default | Description |
| --- | --- | --- | --- |
| `host` | `SCANBRIDGE_FTP_HOST` | required | Host name or address of the server. |
| `port` | `SCANBRIDGE_FTP_PORT` | `21` | Control port. |
| `username` | `SCANBRIDGE_FTP_USERNAME` | `anonymous` | Login name. |
| `password` | `SCANBRIDGE_FTP_PASSWORD` | empty | Password. Secret. |
| `directory` | `SCANBRIDGE_FTP_DIRECTORY` | `/` | Folder to upload into. It must exist. |
| `tls` | `SCANBRIDGE_FTP_TLS` | `true` | Use explicit FTPS. |
| `passive` | `SCANBRIDGE_FTP_PASSIVE` | `true` | Passive mode. Turn off only if the server requires active mode. |

Credentials: the account of the FTP server, for example a user you create in the NAS's FTP service. Give it write access to the target folder only.

```toml
[destinations.nas]
type = "ftp"
host = "nas.example.com"
username = "scanner"
password = "..."          # better: SCANBRIDGE_NAS_PASSWORD_FILE
directory = "/Scans"
```

```sh
SCANBRIDGE_FTP_HOST=nas.example.com
SCANBRIDGE_FTP_USERNAME=scanner
SCANBRIDGE_FTP_PASSWORD_FILE=/run/secrets/ftp_password
SCANBRIDGE_FTP_DIRECTORY=/Scans
```

TLS: with `tls = true` the control and data connections are encrypted (`AUTH TLS`, `PROT P`) and the server's certificate must be valid for `host` and signed by a certificate authority known to the system. A server with a self-signed certificate is refused; use a certificate from a CA (for example Let's Encrypt), or, on a network you fully trust, `tls = false`. If the server does not offer TLS, scanbridge does not fall back to plain FTP and never sends the password. With `tls = false` everything, including the password, travels unencrypted, and scanbridge warns about it at start-up.

How it works: the file is uploaded as `<name>.part` and renamed to its final name when complete, so that a program watching the folder never sees half a file. Before that, scanbridge lists the folder and picks `name (1).ext`, `name (2).ext` and so on if the name is taken. The list is read once, so two scans stored at the same moment from different places could still collide. If the server reports an empty folder as an error (`550 No files found`) this is handled.

Errors: a wrong password, a missing folder (`550`), a refused connection or a failed upload fail the delivery and queue the file. A failed upload removes its `.part` file where the server lets scanbridge do so.

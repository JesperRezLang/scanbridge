# scanbridge

scanbridge makes the "Scan → to PC" button on a Brother network printer work without Brother's software (iPrint&Scan, `brscan-skey`). It registers a name in the printer's "Scan to PC" list, waits for you to pick it on the panel, scans a page over the network and delivers the file to a folder or to Seafile. It runs as a small service (a Docker container, for example) on any always-on machine in your network and uses only the Python standard library.

## Tested printers

| Printer | Status |
| --- | --- |
| Brother DCP-L2620DW (flatbed, one page per press) | ✓ |
| Other Brother models | Probably works with models that support Scan to PC over the network. Reports welcome. |

## How it works

```
 Printer panel                       scanbridge
 -------------                       ----------
 1. register name  <----------------  HTTPS POST /phoenix/mib (SNMP OIDs, Digest login), renewed periodically
 2. press Scan > to PC > name
    UDP 54925  ---------------------> button press, echoed back as acknowledgement
 3. TCP 54921  <--------------------  Brother's scan protocol, returns a JPEG
 4.                                    PDF or JPEG  --->  local folder / Seafile (failed uploads are queued)
```

1. scanbridge writes a registration for each display name to the printer through its web server (`POST https://<printer>/phoenix/mib`, HTTP Digest, default user `Public`, password `0000`). Plain SNMP SET is refused by the firmware. Registrations expire, so they are renewed every `register_duration / 2` seconds.
2. When a name is chosen on the panel, the printer sends a UDP packet to port 54925 (always that port) on the registered host. scanbridge sends the same packet back as acknowledgement.
3. scanbridge connects to TCP 54921 on the printer (Brother's own scan protocol; eSCL does not work in this state) and receives a JPEG.
4. The function chosen on the panel decides the output. By default every function scans at 300 dpi in colour: File → PDF, Image → JPEG, OCR → PDF, E-mail → PDF; format, resolution and colour can be set per function (see [Scan settings per function](#scan-settings-per-function)). OCR is not performed; the PDF contains the scanned image only.
5. The file goes to every destination of that name. A delivery that fails is kept in the retry queue (see below).

The printer's HTTPS certificate is self-signed and is not verified.

The protocol, how it was worked out and how to investigate other models are described in [docs/protocol.md](docs/protocol.md).

## Quick start

You need the printer's address and the LAN address of the machine that runs scanbridge (`192.0.2.20` and `192.0.2.10` below). Scans are saved in `/data/scans` inside the container unless you configure a destination.

### Docker

```sh
docker run -d --name scanbridge --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true --memory 128m --tmpfs /tmp \
  -p 54925:54925/udp \
  -v scanbridge-data:/data \
  -v /srv/scans:/scans \
  -e SCANBRIDGE_PRINTER_HOST=192.0.2.20 \
  -e SCANBRIDGE_DISPLAY_NAME=SCANBRIDGE \
  -e SCANBRIDGE_ADVERTISE_IP=192.0.2.10 \
  -e SCANBRIDGE_LOCAL_PATH=/scans \
  -e TZ=Europe/Stockholm \
  ghcr.io/jesperrezlang/scanbridge:latest
```

`SCANBRIDGE_ADVERTISE_IP` is required with bridge networking, because the container cannot know the LAN address of the host. Alternatively use `--network host` and leave out `-p` and `SCANBRIDGE_ADVERTISE_IP`.

The container runs as uid 10001. Folders you mount must be writable by that user. `TZ` sets the time zone used for the dates in file names (the default is UTC).

To keep the settings in a file, use `--env-file`; see [`examples/scanbridge.env`](examples/scanbridge.env).

### Docker Compose

[`examples/compose.yaml`](examples/compose.yaml) is a complete file with Seafile as the destination and a commented variant that uses a TOML file and host networking. Put the Seafile token in a `.env` file next to it:

```sh
echo 'SCANBRIDGE_SEAFILE_TOKEN=<library API token>' > .env
docker compose up -d
docker compose logs -f
```

On start, the log shows the active configuration and `Registered "SCANBRIDGE" on ...`. After that, the name is listed on the printer under Scan → to PC.

### Without Docker

Python 3.11 or later is required; there are no dependencies.

```sh
SCANBRIDGE_PRINTER_HOST=192.0.2.20 SCANBRIDGE_LOCAL_PATH=$HOME/scans python -m scanbridge
```

Options: `--config PATH` (TOML file, same as `SCANBRIDGE_CONFIG`), `--check` (validate the configuration, print it without secrets and exit; nothing is sent to the printer), `--version`.

## Configuration

Settings come from three layers; a later layer overrides an earlier one:

1. Built-in defaults.
2. A TOML file, given with `--config` or `SCANBRIDGE_CONFIG`.
3. Environment variables.

One printer with one name needs nothing but environment variables. Use a TOML file for several printers, several names or several destinations. Unknown settings in the file are an error, and invalid values are reported at start-up (exit code 2); run `python -m scanbridge --check` to test a configuration.

Any variable can be read from a file by appending `_FILE` to its name, for example `SCANBRIDGE_SEAFILE_TOKEN_FILE=/run/secrets/seafile_token` (Docker secrets). If both are set, the plain variable wins. Trailing line breaks are removed.

### Environment variables

Global settings:

| Variable | Default | Description |
| --- | --- | --- |
| `SCANBRIDGE_CONFIG` | none | Path of a TOML configuration file. |
| `SCANBRIDGE_DATA_DIR` | `./data` (`/data` in the image) | Where scanbridge keeps its own files: the retry queue in `<data_dir>/queue/`, and the scans if no destination is configured. |
| `SCANBRIDGE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. |
| `SCANBRIDGE_RETRY_INTERVAL` | `300` | Seconds between attempts to deliver queued files. |
| `SCANBRIDGE_ADVERTISE_IP` | automatic | Address of this host that the printer should send button presses to. By default the address used on the route to the printer is detected. Set it in Docker bridge networking. |

Printer (these apply to the first printer, which is created if the file has none):

| Variable | Default | Description |
| --- | --- | --- |
| `SCANBRIDGE_PRINTER_HOST` | required | IP address or host name of the printer. |
| `SCANBRIDGE_PRINTER_NAME` | the host | Label for logs and the `{printer}` file name field. |
| `SCANBRIDGE_PRINTER_DRIVER` | `brother` | Printer driver. `brother` is the only one. |
| `SCANBRIDGE_PRINTER_USERNAME` | `Public` | Login of the printer's web interface. |
| `SCANBRIDGE_PRINTER_PASSWORD` | `0000` | Password of the printer's web interface. |
| `SCANBRIDGE_PRINTER_ADVERTISE_IP` | `SCANBRIDGE_ADVERTISE_IP` | Same as the global setting, for this printer only. |
| `SCANBRIDGE_PRINTER_REGISTER_DURATION` | `360` | Seconds a registration lasts. It is renewed every half of this time. |

Target, the name shown on the panel (these apply to the first name of the first printer):

| Variable | Default | Description |
| --- | --- | --- |
| `SCANBRIDGE_DISPLAY_NAME` | `SCANBRIDGE` | Name in the printer's "Scan to PC" list. 1-15 ASCII characters, no `"` or `;`. |
| `SCANBRIDGE_DESTINATIONS` | all configured destinations | Comma-separated destination names, for example `local,seafile`. |
| `SCANBRIDGE_FILENAME` | `Scan_{date:%Y-%m-%d_%H%M%S}` | File name template, see below. |
| `SCANBRIDGE_PROFILE_<FUNCTION>_FORMAT` | `pdf` (`jpg` for `IMAGE`) | `pdf` or `jpg`. `<FUNCTION>` is `FILE`, `IMAGE`, `OCR` or `EMAIL`. |
| `SCANBRIDGE_PROFILE_<FUNCTION>_DPI` | `300` | `100`, `200`, `300` or `600`. |
| `SCANBRIDGE_PROFILE_<FUNCTION>_COLOR` | `color` | `color` or `gray`. |

The `SCANBRIDGE_PROFILE_*` variables are described under [Scan settings per function](#scan-settings-per-function).

Destinations are configured with `SCANBRIDGE_<DESTINATION>_<OPTION>`, where `<DESTINATION>` is the destination's name in upper case. Setting any option of a built-in type (`local`, `seafile`, `webdav`, `paperless` or `ftp`) creates a destination named after its type. The options of `local` and `seafile` are below; all types and their options are in [docs/destinations.md](docs/destinations.md):

| Variable | Default | Description |
| --- | --- | --- |
| `SCANBRIDGE_LOCAL_PATH` | required for `local` | Folder to save scans in. Created if missing. |
| `SCANBRIDGE_SEAFILE_URL` | required for `seafile` | Base URL of the Seafile server, for example `https://seafile.example.com`. |
| `SCANBRIDGE_SEAFILE_TOKEN` | required for `seafile` | Library API token. Never logged. |
| `SCANBRIDGE_SEAFILE_DIRECTORY` | `/` | Folder inside the library. |

If no destination is configured at all, scans are saved in `<data_dir>/scans`. Once any destination exists (for example Seafile only), that default is not added, so add `SCANBRIDGE_LOCAL_PATH` if you also want a local copy.

For destinations defined in a TOML file the same pattern applies with the file's name: `[destinations.nas]` can be set from `SCANBRIDGE_NAS_PATH`. Characters other than letters and digits in a destination name become `_`.

### TOML file

Top-level settings are `data_dir`, `log_level`, `retry_interval` and `advertise_ip`. Printers are `[[printers]]` tables (`host`, `name`, `driver`, `username`, `password`, `advertise_ip`, `register_duration`) with `[[printers.targets]]` tables below them (`name`, `destinations`, `filename`, and `profiles.<function>` tables for the scan settings). Destinations are `[destinations.<name>]` tables; `type` is `local` or `seafile` and defaults to the table's name, so a second folder is a table with `type = "local"`.

```toml
log_level = "INFO"

[[printers]]
host = "192.0.2.20"
name = "Office"

[[printers.targets]]
name = "ARCHIVE"
destinations = ["seafile"]

[[printers.targets]]
name = "NAS"
destinations = ["nas", "local"]
filename = "{date:%Y%m%d-%H%M%S}_{function}"

# A second printer; its targets are registered on that printer only.
[[printers]]
host = "192.0.2.21"
name = "Warehouse"

[destinations.local]
path = "/scans"

[destinations.nas]
type = "local"
path = "/mnt/nas/scans"

[destinations.seafile]
url = "https://seafile.example.com"
token = "..."          # better: SCANBRIDGE_SEAFILE_TOKEN_FILE
directory = "/Scans"
```

[`examples/config.example.toml`](examples/config.example.toml) documents every setting. A target without `destinations` sends to all configured destinations. A printer without targets gets one named `SCANBRIDGE`. Display names must be unique per printer, and each printer needs a different host.

### Scan settings per function

Each panel function (File, Image, OCR, E-mail) has a profile: the file format, the resolution and whether to scan in colour or grayscale. A profile belongs to a target, so different names can scan differently. Only the keys you give replace the defaults:

```toml
[[printers.targets]]
name = "ARCHIVE"

[printers.targets.profiles.file]    # file | image | ocr | email
format = "pdf"     # pdf | jpg
dpi = 300          # 100 | 200 | 300 | 600
color = "color"    # color | gray

[printers.targets.profiles.image]   # only dpi is changed; the format stays jpg
dpi = 600
```

The same settings can be made with environment variables, which apply to the first name of the first printer (like `SCANBRIDGE_DISPLAY_NAME`): `SCANBRIDGE_PROFILE_<FUNCTION>_<KEY>`, for example `SCANBRIDGE_PROFILE_FILE_DPI=200` or `SCANBRIDGE_PROFILE_IMAGE_COLOR=gray`. They override the TOML file.

| Key | Allowed values | Default |
| --- | --- | --- |
| `format` | `pdf`, `jpg` | `pdf` (`jpg` for `image`) |
| `dpi` | `100`, `200`, `300`, `600` | `300` |
| `color` | `color`, `gray` | `color` |

Unknown functions, keys and values are rejected at start-up. Black & white is not offered, because the printer returns it in a raw format instead of JPEG. A `pdf` profile wraps the scanned JPEG in a PDF without re-encoding it. Higher resolutions and colour give larger files and longer scans. `python -m scanbridge --check` lists the profiles that differ from the defaults.

**Only 300 dpi in colour has been verified on real hardware.** The other values (100, 200 and 600 dpi, grayscale) are sent the way Brother's scan protocol defines them and are expected to work on models with the same protocol, but they have not been tested on a printer. If one does not work on yours, or works only partly, please report it with your model.

### Display names

A display name is shown on the printer's panel. It must be 1-15 printable ASCII characters and must not contain `"` or `;`. scanbridge registers every name for all four panel functions (File, Image, OCR, E-mail).

### File names

The template has these fields; the extension (`.pdf` or `.jpg`) is added automatically:

| Field | Value |
| --- | --- |
| `{date:<format>}` | Time of the scan in the container's local time, with a [strftime](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes) format, for example `{date:%Y-%m-%d_%H%M%S}`. |
| `{target}` | The display name. |
| `{function}` | `file`, `image`, `ocr` or `email`. |
| `{printer}` | The printer's `name`. |

The default is `Scan_{date:%Y-%m-%d_%H%M%S}`. Any `/` or `\` in the result is replaced with `_`. If a file with the same name exists in a local folder, scanbridge saves `name (1).pdf`, `name (2).pdf` and so on; Seafile does the same on its side.

## Destinations

| Type | For |
| --- | --- |
| `local` | A folder, including SMB/NFS shares mounted on the host. |
| `seafile` | A Seafile library, with a library API token. |
| `webdav` | Nextcloud, ownCloud, Synology and other WebDAV servers. |
| `paperless` | Paperless-ngx, through its API, with optional tags, correspondent and document type. |
| `ftp` | FTP servers, with explicit FTPS (TLS) by default. |

A scan can go to several destinations; each has its own retry queue. Every option, how to get credentials and how name clashes are handled is described in [docs/destinations.md](docs/destinations.md). The two original types in short:

### local

Saves into a folder, writing through a temporary `.part` file so that folder watchers never see half a file. Option: `path`.

To save to a network share, mount the share on the host (SMB/CIFS or NFS) and mount that folder into the container as a volume, then point `path` at it:

```sh
-v /mnt/nas/scans:/scans -e SCANBRIDGE_LOCAL_PATH=/scans
```

The container user (uid 10001) needs write permission on the share.

### seafile

Uploads to a Seafile library using a library API token. This needs Seafile 9 or later. To create the token, open the library in the web interface, choose Advanced → API Token, and create a token with read-write permission. The token only gives access to that library. Options: `url`, `token`, `directory` (default `/`; the folder should already exist in the library).

scanbridge sends the upload to the server address you configured, even if Seafile returns a different public address in its upload link.

### Retry queue

If a destination cannot be reached, the file is saved in `<data_dir>/queue/<destination>/`, and scanbridge tries again every `retry_interval` seconds (the first attempt comes one interval after start-up), oldest file first, until the file is stored. Keep `<data_dir>` on a volume so that queued files survive a restart. The queue folders are created with mode 0700 and the queued files with 0600. A destination that is down delays only its own queue. Files left in the queue of a destination that you removed from the configuration are not retried.

## Network requirements

| Direction | Port | Purpose |
| --- | --- | --- |
| printer → host | UDP 54925 | Button press. The port is fixed by the printer. |
| host → printer | TCP 54921 | Scanning. |
| host → printer | TCP 443 | Registering names. |

Only one scanbridge (or other Scan-to-PC client) per host can own UDP 54925. Button presses are only accepted from the printer's address. If the printer is on another VLAN, the firewall must allow the three flows above, and `SCANBRIDGE_ADVERTISE_IP` must be an address the printer can reach.

On Windows, Brother's WIA driver (loaded by the Windows Image Acquisition service, `StiSvc`, through `BrNetSti.dll`) already holds UDP 54925. Stop that service, or run scanbridge on another machine.

## Troubleshooting

Set `SCANBRIDGE_LOG_LEVEL=DEBUG` for more detail.

- **The name does not appear on the panel.** Check the log for `Registration on ... failed` or a non-zero status. Make sure the host can reach `https://<printer>` and that `SCANBRIDGE_PRINTER_USERNAME` and `_PASSWORD` match the printer's web interface login (the factory defaults are `Public` / `0000`).
- **Pressing the name does nothing.** The UDP packet is not reaching scanbridge, or it is not being acknowledged. Check that UDP 54925 is open in the firewall and published by Docker, and that `SCANBRIDGE_ADVERTISE_IP` is the host's LAN address, not a container address. The log line `Registered ... button presses go to <ip>:54925` shows what the printer was told. With `DEBUG`, packets from unknown hosts are logged.
- **The panel says "Connecting", then shows an error.** The button press arrived but the scan failed. Check that TCP 54921 from the host to the printer is open (`Scan on ... failed` in the log). A scan that is already running on the same printer causes further presses to be ignored.
- **scanbridge fails to start with "Address already in use".** Another program owns UDP 54925; see the Windows note above.
- **Files are not delivered.** Look for `Storing ... failed ... queued for retry`. The file waits in `<data_dir>/queue/` until the destination works again.
- **Wrong time in file names.** The container uses UTC unless `TZ` is set.

## Upgrading from 0.1

Version 0.1 used unprefixed variable names. They are still read, with a warning in the log, but they will be removed in a future release. If a new name is also set, the new one wins. Old names cannot be read from `*_FILE` variables.

| Old | New |
| --- | --- |
| `PRINTER_IP` | `SCANBRIDGE_PRINTER_HOST` |
| `DISPLAY_NAME` | `SCANBRIDGE_DISPLAY_NAME` |
| `ADVERTISE_IP` | `SCANBRIDGE_ADVERTISE_IP` |
| `PHOENIX_USER` | `SCANBRIDGE_PRINTER_USERNAME` |
| `PHOENIX_PASSWORD` | `SCANBRIDGE_PRINTER_PASSWORD` |
| `REGISTER_DURATION` | `SCANBRIDGE_PRINTER_REGISTER_DURATION` |
| `OUTPUT_DIR` | `SCANBRIDGE_DATA_DIR` (also `SCANBRIDGE_LOCAL_PATH` if Seafile is not configured) |
| `LOG_LEVEL` | `SCANBRIDGE_LOG_LEVEL` |
| `SEAFILE_URL` | `SCANBRIDGE_SEAFILE_URL` |
| `SEAFILE_REPO_TOKEN` | `SCANBRIDGE_SEAFILE_TOKEN` |
| `SEAFILE_DIR` | `SCANBRIDGE_SEAFILE_DIRECTORY` |

The `SEAFILE_*` names are translated only when both `SEAFILE_URL` and `SEAFILE_REPO_TOKEN` are set, which is how 0.1 decided to use Seafile.

The defaults changed: if you did not set `DISPLAY_NAME`, the name on the panel is now `SCANBRIDGE`, and file names are now `Scan_{date:%Y-%m-%d_%H%M%S}`. Set `SCANBRIDGE_DISPLAY_NAME` and `SCANBRIDGE_FILENAME` to keep your old ones.

Files that 0.1 left in `<data_dir>/pending` (failed Seafile uploads) are moved to `<data_dir>/queue/seafile/` at start-up when a `seafile` destination is configured.

## Security

scanbridge listens only on UDP 54925 and accepts packets from the configured printers only. The scan itself (TCP 54921) is not encrypted, and the printer's HTTPS certificate is not verified, so keep the printer and the scanbridge host on a trusted network.

- Change the printer's web password from the factory default `0000` and set `SCANBRIDGE_PRINTER_PASSWORD`.
- Use `https://` and FTPS for destinations; scanbridge warns at start-up about plain `http://` and FTP without TLS (except to the local host).
- Give tokens through `*_FILE` (Docker secrets) and use the hardening options in [`examples/compose.yaml`](examples/compose.yaml).

The threat model and how to report a vulnerability are in [SECURITY.md](SECURITY.md).

## Roadmap

- More destinations: SFTP, S3, SMB without mounting.
- More printer models.
- Multi-page scans (document feeder and "scan another page").

## Development

```sh
python -m unittest discover -s tests -v   # tests
ruff check .                               # lint
SCANBRIDGE_CONFIG=examples/config.example.toml python -m scanbridge --check
```

### Releasing

1. Set `__version__` in `scanbridge/__init__.py` to the new version and merge that to `main`.
2. In the Actions tab, open **release**, click **Run workflow** and enter the same version (for example `0.3.0`).

The workflow creates the tag `v0.3.0` and a GitHub release with generated notes, and publishes `ghcr.io/jesperrezlang/scanbridge:0.3.0` and `:latest`.

## License

MIT, see [LICENSE](LICENSE).

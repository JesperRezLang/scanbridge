# Security policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead: open the repository's **Security** tab, choose **Report a vulnerability** and describe the problem. You will get an answer there.

## Supported versions

Only the latest release receives fixes.

## Threat model

scanbridge is meant to run on a trusted home or office network next to the printer.

### What is exposed

- UDP 54925, which receives the button press from the printer. There is no web interface and no other listening port.
- Queued scans on disk in `<data_dir>/queue/` (in the container: the `/data` volume), until they are delivered.

### What is not encrypted, and why

| Traffic | Reason |
| --- | --- |
| Button press (UDP 54925) and scan (TCP 54921) | Brother's own protocol; the printer firmware offers no encryption and it cannot be changed. The scanned image crosses the network in clear text. |
| Registration on the printer (HTTPS, port 443) | The printer uses a self-signed certificate, so scanbridge does not verify it. The login (HTTP Digest) is still sent, so a machine in the path could impersonate the printer. |
| Destinations with an `http://` URL, or FTP with `tls = false` | The administrator's choice. Credentials and documents are then sent in clear text. |

### What scanbridge does

- Answers UDP packets only from the IP addresses of the configured printers, and starts a scan only for display names it has registered. Anything else is ignored.
- Acknowledges retransmitted packets without scanning again, and runs one scan at a time per printer.
- Validates display names at start-up so that they cannot break the registration string sent to the printer.
- Replaces path separators and control characters in file names.
- Runs as the non-root user with uid 10001 in the container image.
- Masks secrets (destination tokens) in the log and in `--check` output.
- Reads any setting from a file with `*_FILE` (Docker secrets), so secrets need not be in environment variables.
- Creates the queue folders with mode 0700 and writes queued files with mode 0600. Files saved by the `local` destination keep the default permissions, because other programs may need to read them.
- Logs a warning at start-up for each destination with an `http://` URL or FTP without TLS, except to `localhost`, `127.0.0.0/8` and `::1`.
- Verifies the TLS certificates of destinations with `https://` URLs and of FTPS servers (Python's default context).

### Recommendations

- Change the printer's web password from the factory default `0000` and set `SCANBRIDGE_PRINTER_PASSWORD` to match.
- Keep the printer and the scanbridge host on a trusted network or VLAN. Anyone on the path between the printer and the host can read the scans.
- Use `https://` and FTPS for destinations.
- Pass tokens with `*_FILE` or Docker secrets instead of plain environment variables, which are visible in `docker inspect`.
- Use the hardened settings in [`examples/compose.yaml`](examples/compose.yaml): read-only root file system, no capabilities, `no-new-privileges`, a memory limit.
- Keep the data volume private to the account that runs scanbridge.

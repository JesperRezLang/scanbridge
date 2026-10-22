# Brother "Scan to PC" protocol

This document describes how the "Scan → to PC" button on a Brother network printer works on the wire, how that was worked out, and how scanbridge implements it. It is meant for people who want to verify the findings, extend them, or add support for other printer models.

Everything here was found by observing Brother's own software and the printer's network traffic. Nothing is official. Where something is unknown or only partly verified, the text says so.

Addresses in all examples are documentation addresses: the printer is `192.0.2.20`, the machine running scanbridge is `192.0.2.10`, and a Windows PC is `192.0.2.30`. The names shown on the printer panel are `SCANBRIDGE` or `MYPC`.

## Contents

1. [Overview](#1-overview)
2. [How Brother's software does it](#2-how-brothers-software-does-it)
3. [Why eSCL is not enough](#3-why-escl-is-not-enough)
4. [Registration](#4-registration)
5. [The button press](#5-the-button-press)
6. [The scan protocol on TCP 54921](#6-the-scan-protocol-on-tcp-54921)
7. [PDF wrapping](#7-pdf-wrapping)
8. [Open questions](#8-open-questions)
9. [Tools in this repository](#9-tools-in-this-repository)
10. [Investigating another model](#10-investigating-another-model)

## 1. Overview

The only printer tested is a **Brother DCP-L2620DW**: a monochrome laser multifunction printer with a flatbed scanner and no automatic document feeder (ADF). It also supports eSCL (AirScan/Mopria).

Making the panel button work involves three separate steps:

| Step | Transport | Purpose |
| --- | --- | --- |
| Registration | HTTPS (or SNMP) to the printer | The host tells the printer that it exists, so that its name appears in the panel's "to PC" list. |
| Button press | UDP, printer → host, port 54925 | The printer reports which name and function the user picked. The host acknowledges it. |
| Scan | TCP, host → printer, port 54921 | The host pulls the image from the printer using Brother's own protocol. |

```
 Printer                                  Host (scanbridge)
 -------                                  -----------------
 (register)  <----- HTTPS POST /phoenix/mib, renewed periodically
 user picks name on the panel
 UDP 54925   -----> button packet
             <----- same packet back (acknowledgement)
 TCP 54921   <----- scan commands; the printer answers with a JPEG
```

## 2. How Brother's software does it

This is a generic picture of Brother's Windows software (iPrint&Scan and the drivers installed with it), pieced together from the installed files and from traffic captures. Brother's default install location is under `C:\Program Files (x86)\Brother\`.

```
Printer panel: Scan > to PC > <PC name>
      |  UDP to the PC, port 54925
      v
Windows Image Acquisition service (StiSvc) loads Brother's STI driver, BrNetSti.dll.
The driver holds UDP port 54925 and raises an STI event
(ScanToFile / ScanToImage / ScanToOCR / ScanToEmail).
      |
      v
BrScanKeyEventMgr: forwards the event to the registered application
      |  named pipe, JSON "push scan" command
      v
iPrint&Scan (WPF/.NET application)
      |
      v
ScanCoreApi.dll (native)  ->  BsNetDevAccs.dll (TCP/UDP/SNMP)
                          ->  BsUsbDevAccs.dll (USB)
```

Points worth knowing:

- iPrint&Scan itself is a .NET application. Its C# layer exposes the scan API and its parameter names (resolution, colour mode, area and so on), but **the network protocol is in the native DLLs** (`ScanCoreApi.dll`, `BsNetDevAccs.dll`), not in the managed code. Reading the .NET code tells you which parameters exist, not what goes over the wire.
- The Windows driver `BrNetSti.dll` is what owns UDP 54925 on a PC where Brother's software is installed. It also contains the registration string format (see below). The driver's own settings are stored in the registry under the standard imaging-device class key; they include the PC name shown on the printer (`PCNAME`), a flag that enables the scan key (`ENABLESKEY`) and the printer address.
- SNMP access to the printer uses Brother's private enterprise tree, `1.3.6.1.4.1.2435.*`.
- Open-source Linux tools such as `brscan-skey` implement the same feature for Brother's Linux scanner drivers and are a useful cross-reference.

## 3. Why eSCL is not enough

The DCP-L2620DW supports eSCL. `GET /eSCL/ScannerCapabilities` reports, in summary:

- colour, grayscale and black & white;
- 100, 200, 300 and 600 dpi;
- JPEG and PDF output;
- flatbed up to A4/Letter (2550 × 3507 pixels at 300 dpi).

For a scan started from the computer, eSCL works fine: `POST /eSCL/ScanJobs`, then `GET …/NextDocument`.

It **cannot be used after a button press**. While the panel shows "Connecting", the printer answers `500` to `POST /eSCL/ScanJobs`. It appears to be waiting for Brother's own protocol on TCP 54921 (section 6).

## 4. Registration

For a name to appear under Scan → to PC, the host must write a registration string to the printer. One string is written per panel function (File, Image, OCR, E-mail).

### OIDs

| OID | Meaning |
| --- | --- |
| `1.3.6.1.4.1.2435.2.3.9.2.11.1.1.0` | register |
| `1.3.6.1.4.1.2435.2.3.9.2.11.1.2.0` | unregister (string format not yet known) |

### Registration string

```
TYPE=BR;BUTTON=SCAN;USER="SCANBRIDGE";FUNC=FILE;HOST=192.0.2.10:54925;APPNUM=1;DURATION=360;BRID=;CC=1;
```

The field order follows `BrNetSti.dll`. scanbridge sends exactly this layout.

| Field | Meaning |
| --- | --- |
| `TYPE=BR` | Constant. |
| `BUTTON=SCAN` | The button the registration is for. |
| `USER` | The name shown on the panel, in double quotes. scanbridge limits it to 1–15 ASCII characters without `"` or `;`. |
| `FUNC` | The panel function: `FILE`, `IMAGE`, `OCR` or `EMAIL`. The printer lists each name once per function. |
| `HOST` | `<ip>:<port>` of the host that should receive the button press. The IP is used; the port is ignored (the printer always sends to 54925, see section 5). |
| `APPNUM` | A number identifying the registration. The printer returns it in the button packet. Brother's driver numbers its four functions 1–4; scanbridge uses 1–4 in the order FILE, IMAGE, OCR, EMAIL. The numbering is chosen by the registering side and is not fixed by the printer. |
| `DURATION` | Lifetime of the registration in seconds. |
| `BRID` | Left empty. |
| `CC` | `1`. Purpose not known. |

### Expiry

A registration disappears after `DURATION` seconds, so it has to be renewed before then. scanbridge uses 360 seconds and renews every half of that. Writing the same string again is how renewal is done.

### SNMP SET is refused

Writing the OID with a normal SNMP SET does not work on the tested firmware: SNMPv1 returns `noSuchName` and SNMPv2c returns `noAccess`, both with the `public` and `internal` communities. Reading with `public` works.

### Working path: MIB via Phoenix

The printer's web server exposes the same OIDs through a JSON endpoint, which Brother's software uses (the library is called "MIB via Phoenix"):

```
POST https://192.0.2.20/phoenix/mib
Content-Type: application/json
Authorization: HTTP Digest, user "Public"

{"request":[{"key":"1.3.6.1.4.1.2435.2.3.9.2.11.1.1.0","string_value":"TYPE=BR;BUTTON=SCAN;USER=\"SCANBRIDGE\";FUNC=FILE;..."}]}
```

Response:

```
{"response":[{"key":"1.3.6.1.4.1.2435.2.3.9.2.11.1.1.0","status":0}]}
```

- Several entries can be sent in one request; the response has one `status` per entry. `0` means success.
- The certificate is self-signed.
- The login is the web interface account. Brother's factory default is user `Public`, password `0000`. If the password has been changed, use the changed one.

### Unregistering

Sending the unregister OID (`…11.1.2.0`) with the same string as for registration returned status `-2`. The correct value is not known. In practice it does not matter much, because a registration expires on its own after `DURATION` seconds.

## 5. The button press

When the user selects a registered name on the panel, the printer sends one UDP packet to the registered host.

- Source: the printer, a random source port.
- Destination: port **54925** on the host named in `HOST=`. It is always 54925; the port written in `HOST=` is ignored.
- Payload: a 4-byte header followed by `KEY=VALUE;` text.

Representative example (text part):

```
TYPE=BR;BUTTON=SCAN;USER="SCANBRIDGE";FUNC=FILE;HOST=192.0.2.10:54925;APPNUM=1;P1=0;P2=0;P3=0;P4=0;REGID=1234;SEQ=1;
```

preceded by the bytes `02 00 <len> 30`. The third byte looks like a length of the packet; scanbridge does not use the header and simply skips the first four bytes.

| Field | Meaning |
| --- | --- |
| `USER` | The registered name that was selected. |
| `FUNC` | The panel function that was selected. scanbridge uses it to choose the output format and scan settings. |
| `APPNUM` | The `APPNUM` of the matching registration. |
| `HOST` | Echo of the registered host. |
| `P1`–`P4` | Unknown. All `0` for File with default settings. |
| `REGID` | An identifier chosen by the printer. |
| `SEQ` | A sequence number. |

### Acknowledgement

The host must acknowledge the press by sending **exactly the same packet back** to the source address and port it came from. scanbridge's handler does just that (`sock.sendto(data, src)`) and then starts the scan. While the scan is being set up, the panel shows "Connecting".

### Consequence: one program per host owns UDP 54925

Since the destination port is fixed, only one program per host can receive button presses. On a Windows PC with Brother's software installed, the WIA service (`StiSvc` via `BrNetSti.dll`) holds the port, and a second listener cannot bind to it. Either run scanbridge on a machine without Brother's driver, or disable Brother's driver on that PC. (A listener using `SO_REUSEADDR` can observe packets in parallel for investigation, which is what `poc/sniff_54925.py` does, but only one program should acknowledge them.)

A related observation: Brother's WIA driver on a PC answered a press even though the selected `USER` was not the name registered by that PC. Keep this in mind when testing next to a PC that runs Brother's software.

## 6. The scan protocol on TCP 54921

After the acknowledgement, the host connects to TCP port 54921 on the printer. This is Brother's own scan protocol. It was mapped from a packet capture of iPrint&Scan scanning from the DCP-L2620DW, and implemented in `scanbridge/drivers/brother/protocol.py`.

### Framing

**Greeting.** On connect the printer sends `+OK 200\r\n` (9 bytes).

**Commands** (host → printer) are text:

```
ESC <name> \n [KEY=VALUE \n ...] 0x80
```

that is, byte `0x1b`, the command name, a newline, zero or more `KEY=VALUE` lines each ending in a newline, and a terminating `0x80` byte.

**Replies** (printer → host) are framed as:

```
00 <3 ASCII letters> <u32 little-endian length> <data>
```

where the three letters repeat the command name (`QDI`, `GKP`, `SSP`, `GCP`). The reply to `Q` is the exception: it has its own frame, `c1 00 <n> ...`, where `n` counts bytes starting at the length byte itself (75 bytes in total on the DCP-L2620DW).

### Command sequence

| # | Command | Parameters | Reply |
| --- | --- | --- | --- |
| 1 | `Q` | none | special frame, 75 bytes |
| 2 | `QDI` | none | `QDI` frame: a TLV list with the model and other device data |
| 3 | `GKP` | none | `GKP` frame, probably the key (button) settings; not interpreted |
| 4 | `SSP` | scan parameters, see below | `SSP` frame |
| 5 | `GCP` | none | `GCP` frame containing an ICC colour profile; not used |
| 6 | `XSC` | `RESO`, `AREA`, `MODE` | image blocks, see below |

All steps are done in this order on one TCP connection, shortly after the button press has been acknowledged.

### SSP: scan parameters

scanbridge sends these `SSP` parameters (values as sent for a 300 dpi colour scan):

| Key | Value | Notes |
| --- | --- | --- |
| `OS` | `WIN` | |
| `PSRC` | `AUTO` | Paper source. |
| `RESO` | `300,300` | Resolution in dpi, x and y. |
| `CLR` | `C24BIT` | Colour mode, see below. |
| `AREA` | `NORMAL` | |
| `MRGN` | `0,0,0,0` | |
| `DPLX` | `OFF` | Duplex. |
| `BRIT`, `CONT` | `50`, `50` | Brightness and contrast. |
| `COMP` | `JPEG` | Compression of the returned image. |
| `JSF` | `420` | Presumably JPEG chroma subsampling. |
| `IPRC` | `NORMAL` | |
| `PTYPE` | `NORMAL` | |
| `PAGE` | `0` | |
| `LONG`, `CARR`, `RMGC`, `DTDF`, `DT4V`, `DSKW`, `LSMD`, `RMBP`, `RMMR`, `GMMA`, `TONE`, `QTFD`, `ATCN`, `ATCRP` | `OFF` | Various image processing and feed options, all switched off. |

The meaning of several keys is inferred from their names only. The values above are those iPrint&Scan sent in the capture, apart from `RESO` and `CLR`, which scanbridge varies with the scan settings.

**Colour modes.** `CLR=C24BIT` is 24-bit colour; this is the value verified against a real printer. scanbridge also offers `CLR=GRAY64` (8-bit grayscale), which has **not** been verified on hardware. Black & white (a 1-bit mode) was observed to return an image in a format that is not JPEG, and is not supported.

### XSC: starting the scan

```
ESC XSC \n
RESO=300,300 \n
AREA=0,0,2409,3437 \n
MODE=NORMAL \n
0x80
```

`AREA` is four numbers in pixels at the chosen resolution (not millimetres). The values captured from iPrint&Scan for the whole flatbed at 300 dpi were `0,0,2409,3437`, which produced a 2416 × 3437 pixel image (the width is padded by the scanner). For other resolutions scanbridge scales the 300 dpi area linearly (`round(value × dpi / 300)`).

### Image blocks

After `XSC` there is no framed reply. The image arrives as a series of blocks:

```
offset  size  meaning
0       1     unknown
1       1     record type: 0x02 = image data, 0x21 = end record
2       4     unknown
6       2     u16 little-endian: length of the data that follows
8       6     unknown
14      n     data (a slice of the JPEG)
```

- Each image block has a 14-byte header with byte 1 equal to `0x02`. The u16 at bytes 6–7 is the payload length. The payloads, concatenated, are the image.
- A header whose byte 1 is not `0x02` ends the transfer. Byte 1 = `0x21` is the end-of-page record; anything after it is ignored by scanbridge.

The result is a **plain JPEG** (it starts with `ff d8`). scanbridge treats a result that does not start with the JPEG signature as an error.

## 7. PDF wrapping

For PDF output scanbridge embeds the scanner's JPEG unchanged: a one-page PDF with the JPEG stored as an image XObject with `/Filter /DCTDecode`, so the image is not re-encoded. The page size is the pixel size divided by the scan resolution, converted to points (`pixels × 72 / dpi`). The colour space (`DeviceRGB` or `DeviceGray`) is taken from the number of components in the JPEG's SOF marker. The code is in `scanbridge/pdf.py`.

No OCR is performed; an OCR panel function produces the same image-only PDF.

## 8. Open questions

- **Unregister format.** The string for `…11.1.2.0` is not known. The registration string returned status `-2`.
- **`P1`–`P4`.** Their meaning in the button packet is unknown. They are all `0` for File with default panel settings. They may carry panel settings such as colour, resolution or file type, which would be worth checking by changing the settings on the panel and pressing the button again.
- **`CC`, `BRID`, `JSF`** and several other `SSP` keys: purpose inferred or unknown.
- **Other models.** Only the DCP-L2620DW is tested. Models with the same "Scan to PC" feature probably behave the same, but parameter values and the `Q`/`QDI` replies may differ.
- **ADF and duplex.** The tested model has no feeder, so `PSRC`, `DPLX` and multi-page behaviour (what happens after the end-of-page record) are unexplored.
- **Black & white.** The returned format is not JPEG and has not been decoded.
- **`GRAY64`.** Used by scanbridge, not verified.
- **Firmware variation.** SNMP SET being refused and the Phoenix endpoint working was observed on one firmware version only.

## 9. Tools in this repository

| File | What it does |
| --- | --- |
| `poc/skey_listen.py` | Proof of concept: registers a name for all four panel functions (via Phoenix, falling back to SNMP SET), listens on a UDP port, prints and logs every packet it receives, renews the registration while running and tries to unregister on exit. |
| `poc/sniff_54925.py` | Listens on UDP 54925 with `SO_REUSEADDR`, so it can run next to another program (such as Brother's WIA service) that owns the port, and logs the packets it sees. |
| `recon/scripts/snmpget.py` | Minimal dependency-free SNMPv1/v2c GET and GETNEXT client, also providing the BER helpers used by `poc/skey_listen.py`. |
| `recon/scripts/pcap_udp.py` | Lists the UDP packets (text and hex) in a capture file, optionally filtered by port. |
| `recon/scripts/pcap_tcp.py` | Reassembles the TCP streams of a capture file into one binary file per direction and writes a timeline of the segments. |
| `recon/scripts/strs.py` | Prints the ASCII and UTF-16LE strings in binary files that match a regular expression; useful for finding OIDs and format strings in Brother's DLLs. |

The pcap scripts expect Ethernet/IPv4 captures in pcapng format, which is the default in current Wireshark and the format written by pktmon; classic .pcap files are not read.

## 10. Investigating another model

A rough procedure for a printer that scanbridge does not yet support:

1. **Capture a working scan.** Install iPrint&Scan on a Windows PC and capture the traffic between the PC and the printer while you register and then use Scan → to PC (Wireshark, or `pktmon` on Windows, saved as a capture file). Capture on the PC itself, or on a mirror port, with the printer's address as the filter.
2. **Find the registration.** Look for the registration write: an HTTPS POST to `/phoenix/mib` (encrypted, so also check Brother's DLLs with `strs.py`), or SNMP SET to `1.3.6.1.4.1.2435.2.3.9.2.11.1.1.0` on older firmware. Compare the string with section 4: field order, `FUNC` values, `APPNUM` numbering, `DURATION`.
3. **Check the button packet.** Use `recon/scripts/pcap_udp.py capture.pcapng 54925` to list the packets, or run `poc/skey_listen.py` against the printer from a host that does not run Brother's software and press the button. Compare `FUNC`, `APPNUM`, `P1`–`P4`, `REGID` and `SEQ`.
4. **Compare the TCP session.** Run `recon/scripts/pcap_tcp.py capture.pcapng out 54921` and compare the reassembled streams with section 6: greeting, command order, `SSP` keys and values, `XSC` `AREA`, and the framing of the image blocks. Differences in `SSP` or `XSC` parameters are the most likely thing to change between models.
5. **Test with scanbridge.** Run `python -m scanbridge` with `SCANBRIDGE_LOG_LEVEL=DEBUG` against the printer and press the button. If the scan fails, the log shows which command's reply did not match the expected framing.

Useful variations to try, each in a separate capture: another resolution, grayscale and black & white, a different panel function, and any document-feeder or duplex setting the model offers. When you find something, please add it to this document.

# Manifest Validator CLI

A command-line tool for validating **HLS** (`.m3u8`) and **DASH** (`.mpd`) streaming manifests. For each manifest it fetches the playlist, parses all streams, validates segment URLs, detects codecs and DRM, and produces both an **HTML report** and a **CSV summary**.

---

## Features

- Auto-detects HLS or DASH from the URL
- **HLS**: Master playlist parsing, video / audio / subtitle / I-frame variant support, CMAF compliance check, DRM key detection
- **DASH**: XML well-formedness check, Schematron validation, AdaptationSet / Representation parsing, init + media segment validation, CMAF detection
- Codec string translation (AVC, HEVC, AV1, AAC, AC3, Dolby Vision, DTS …)
- DRM detection (Widevine, PlayReady, FairPlay, ClearKey, Verimatrix)
- Generates a styled **HTML report** and a flat **CSV summary** per run

---

## Example Reports

> Preview links use [htmlpreview.github.io](https://htmlpreview.github.io) to render the HTML directly in your browser.

| Stream | Format | HTML Report | CSV |
|--------|--------|-------------|-----|
| [Tears of Steel](https://demo.unified-streaming.com/k8s/features/stable/video/tears-of-steel/tears-of-steel.ism/.m3u8) | HLS | [View report](https://htmlpreview.github.io/?https://github.com/Epifanio-Silva/manifest-validator/blob/main/examples/hls_example.html) | [hls_example.csv](examples/hls_example.csv) |
| CMAF Live Stream | DASH | [View report](https://htmlpreview.github.io/?https://github.com/Epifanio-Silva/manifest-validator/blob/main/examples/dash_example.html) | [dash_example.csv](examples/dash_example.csv) |

---

## Requirements

- Python 3.8+
- Java (required for Schematron validation of DASH manifests — optional, validation is skipped gracefully without it)

Python packages:

```
requests
m3u8
lxml
```

---

## Installation

```bash
git clone https://github.com/Epifanio-Silva/manifest-validator.git
cd manifest-validator

# Create and activate a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate          # macOS / Linux
# venv\Scripts\activate           # Windows

pip install -r requirements.txt
```

---

## Usage

```bash
python validate.py <URL> [OPTIONS]
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--segments N` / `-s N` | `3` | Number of segments to validate per stream |
| `--output-dir DIR` / `-o DIR` | `reports/` | Directory to write reports to |
| `--name NAME` / `-n NAME` | auto-timestamped | Base filename for output files (no extension) |

### Examples

**Validate an HLS manifest:**
```bash
python validate.py https://example.com/stream.m3u8
```

**Validate a DASH manifest, check 5 segments:**
```bash
python validate.py https://example.com/stream.mpd --segments 5
```

**Custom output directory and filename:**
```bash
python validate.py https://example.com/stream.mpd --segments 3 --output-dir ./output --name my_stream
```

Each run produces two files in the output directory:
```
output/
  my_stream.html   ← full styled validation report
  my_stream.csv    ← flat summary, one row per stream / representation
```

---

## Project Structure

```
manifest-validator/
├── validate.py          # CLI entry point — auto-detects HLS / DASH
├── validate_hls.py      # HLS playlist validator
├── validate_dash.py     # DASH MPD validator
├── codec_profile.py     # Codec string parser
├── schematron.sch       # DASH Schematron schema (main)
├── ac4-generic.sch      # AC-4 audio rules (included by schematron.sch)
├── dvb-dash.sch         # DVB-DASH rules (included by schematron.sch)
├── helper-functions.sch # Shared Schematron helpers
├── schxslt-cli.jar      # SchXslt Java runner for Schematron validation
├── requirements.txt
└── examples/
    ├── hls_example.html
    ├── hls_example.csv
    ├── dash_example.html
    └── dash_example.csv
```

---

## Notes

- **Schematron validation** requires Java and `schxslt-cli.jar` (included). If Java is not installed the tool continues without it and logs a warning.
- **Segment type detection** (CMAF vs Fragmented MP4) uses pure Python ISO BMFF box parsing — no external tools required.
- Reports are written to the `reports/` directory by default (excluded from git). The `examples/` directory contains pre-generated reports committed to the repo.

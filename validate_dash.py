#!/usr/bin/env python3

"""
validate_dash.py

Validates a DASH MPD manifest: downloads the MPD, validates XML structure,
runs Schematron validation (if Java and schxslt-cli.jar are available),
parses all AdaptationSets/Representations, validates segment URLs, and
generates an HTML report and structured CSV data.

Usage (direct):
    python validate_dash.py <MPD_URL> <num_segments> <REPORT_FILE.html>

Usage (via validate.py):
    python validate.py <MPD_URL> --segments <N>
"""

import argparse
import csv
import logging
import os
import re
import shutil
import sys
import tempfile
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Dict
from urllib.parse import urljoin

import requests
from lxml import etree

import codec_profile


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    logger = logging.getLogger('validate_dash')
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger


logger = setup_logging()


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def is_numeric(value: str) -> bool:
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False


def get_drm_name(uri: str, known_uuids: List[str], drm_names: List[str]) -> str:
    uri_lower = uri.lower()
    for uuid, name in zip(known_uuids, drm_names):
        if uuid.lower() == uri_lower:
            return name
    return 'Unknown DRM'


def get_codec_translation(codec_string: str) -> str:
    if not codec_string or codec_string.lower() == 'n/a':
        return 'N/A'
    return codec_profile.parse_codec(codec_string)


def download_file(url: str, dest_path: Path, timeout: int = 10) -> Tuple[Optional[int], Optional[str]]:
    try:
        response = requests.get(url, allow_redirects=True, timeout=timeout)
        status_code = response.status_code
        if status_code == 200:
            with open(dest_path, 'wb') as f:
                f.write(response.content)
            return status_code, response.url
        else:
            logger.error(f'HTTP error while downloading {url}: {status_code} {response.reason}')
            return status_code, None
    except requests.RequestException as e:
        logger.error(f'Request exception while downloading {url}: {e}')
        return None, None


def format_http_status(status: Optional[int]) -> str:
    logger.debug(f'Formatting HTTP status: {status}, type: {type(status)}')
    try:
        if status == 200:
            return f'<span class="status-ok">{status} OK</span>'
        elif status == 404:
            return f'<span class="status-error">{status} Not Found</span>'
        elif status is not None:
            return f'<span class="status-error">{status}</span>'
        else:
            return '<span class="status-error">Network Error</span>'
    except (ValueError, TypeError):
        return '<span class="status-error">N/A</span>'


def extract_xml_attribute(tree: etree._ElementTree, xpath_expr: str, namespaces: Dict[str, str], default: str = '') -> str:
    try:
        result = tree.xpath(xpath_expr, namespaces=namespaces)
        if result:
            return result[0]
        return default
    except etree.XPathError as e:
        logger.error(f"XPath error '{xpath_expr}': {e}")
        return default


# ---------------------------------------------------------------------------
# DashValidator
# ---------------------------------------------------------------------------

class DashValidator:
    def __init__(self, mpd_url: str, num_segments: int, report_file: str):
        self.mpd_url = mpd_url
        self.num_segments = num_segments
        self.report_file = report_file
        self.temp_dir = tempfile.TemporaryDirectory()
        self.error_messages: List[str] = []
        self.dependencies_ok = True
        self.mpd_downloaded = False
        self.schematron_validated = False
        self.FINAL_URL = ''
        self.BASE_URL = ''
        self.mpd_http_status = 'N/A'
        self.nsmap: Dict[str, str] = {}
        self.known_uuids = [
            'urn:mpeg:dash:mp4protection:2011',
            'urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed',
            'urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95',
            'urn:uuid:5e629af5-38da-4063-8977-97ffbd9902d4',
            'urn:uuid:00000000-0000-0000-0000-000000000000',
        ]
        self.drm_names = [
            'Common Encryption (CENC)',
            'Widevine',
            'PlayReady',
            'FairPlay',
            'ClearKey',
        ]
        self.drm_schemes: List[str] = []
        self.drm_scheme_names: List[str] = []
        self.adaptation_set_data: List[str] = []
        self.csv_rows: List[Dict] = []
        self.closed_captions_detected = False

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def log_error(self, message: str):
        self.error_messages.append(message)
        logger.error(f'Error: {message}')

    # ------------------------------------------------------------------
    # Dependency / download checks
    # ------------------------------------------------------------------

    def check_dependencies(self):
        schxslt_path = Path('schxslt-cli.jar')
        schematron_path = Path('schematron.sch')
        if not schxslt_path.is_file():
            self.log_error(
                f'schxslt-cli.jar not found at {schxslt_path}. '
                'Schematron validation will be skipped.'
            )
            self.dependencies_ok = False
        if not schematron_path.is_file():
            self.log_error(
                f'Schematron schema not found at {schematron_path}. '
                'Schematron validation will be skipped.'
            )
            self.dependencies_ok = False

    def download_mpd(self):
        logger.info(f'Downloading MPD from URL: {self.mpd_url}')
        manifest_path = Path(self.temp_dir.name) / 'manifest.mpd'
        status_code, final_url = download_file(self.mpd_url, manifest_path)
        if status_code is None:
            self.log_error(f'Failed to download the MPD file: {self.mpd_url}')
            self.mpd_downloaded = False
            return

        self.mpd_http_status = status_code
        self.FINAL_URL = final_url if final_url else self.mpd_url

        if not manifest_path.is_file() or manifest_path.stat().st_size == 0:
            self.log_error('Downloaded MPD file is empty or missing.')
            self.mpd_downloaded = False
            return

        if self.is_well_formed_xml(manifest_path):
            root = self.get_root_element(manifest_path)
            if root is not None:
                root_local_name = etree.QName(root).localname
                if root_local_name != 'MPD':
                    self.log_error('Root element is not MPD — not a valid DASH manifest.')
                    self.mpd_downloaded = False
                    return
        else:
            self.mpd_downloaded = False
            return

        self.closed_captions_detected = self.detect_closed_captions(manifest_path)
        self.mpd_downloaded = True

    # ------------------------------------------------------------------
    # XML helpers
    # ------------------------------------------------------------------

    def is_well_formed_xml(self, file_path: Path) -> bool:
        try:
            etree.parse(str(file_path))
            logger.info('MPD is well-formed XML.')
            return True
        except etree.XMLSyntaxError as e:
            self.log_error(f'MPD is not well-formed XML: {e}')
            return False

    def get_root_element(self, file_path: Path) -> Optional[etree._Element]:
        try:
            tree = etree.parse(str(file_path))
            return tree.getroot()
        except etree.XMLSyntaxError as e:
            self.log_error(f'Failed to parse MPD root element: {e}')
            return None

    def detect_closed_captions(self, manifest_path: Path) -> bool:
        try:
            tree = etree.parse(str(manifest_path))
            root = tree.getroot()
            nsmap = root.nsmap.copy()
            if None in nsmap:
                nsmap['ns'] = nsmap.pop(None)
            else:
                nsmap['ns'] = 'urn:mpeg:dash:schema:mpd:2011'
            self.nsmap = nsmap
            captions = tree.xpath(
                "//ns:AdaptationSet[@mimeType='text/vtt' or @mimeType='application/ttml+xml']",
                namespaces=self.nsmap
            )
            if captions:
                logger.info('Closed Captions detected.')
                return True
            logger.info('No Closed Captions detected.')
            return False
        except (etree.XMLSyntaxError, etree.XPathError, Exception) as e:
            logger.error(f'Error during closed captions detection: {e}')
            return False

    # ------------------------------------------------------------------
    # Schematron validation
    # ------------------------------------------------------------------

    def validate_mpd_schematron(self):
        logger.info('Validating MPD against Schematron schema...')
        schematron_path = Path('schematron.sch')
        schxslt_path = Path('schxslt-cli.jar')
        schematron_output = Path(self.temp_dir.name) / 'schematron_output.xml'

        try:
            subprocess.run([
                'java', '-jar', str(schxslt_path),
                '-d', str(Path(self.temp_dir.name) / 'manifest.mpd'),
                '-s', str(schematron_path),
                '-o', str(schematron_output)
            ], check=True)

            tree = etree.parse(str(schematron_output))
            failed_asserts = tree.xpath(
                '//svrl:failed-assert',
                namespaces={'svrl': 'http://purl.oclc.org/dsdl/svrl'}
            )
            if failed_asserts:
                logger.info('Schematron Validation Failed')
                for fa in failed_asserts:
                    text = fa.findtext('svrl:text', namespaces={'svrl': 'http://purl.oclc.org/dsdl/svrl'}) or 'No message provided.'
                    location = fa.get('location', 'Unknown location')
                    test = fa.get('test', 'No test provided')
                    self.log_error(f'Schematron Validation Failed at {location}: {text}. Test: {test}')
                self.schematron_validated = False
            else:
                logger.info('Schematron Validation Passed')
                self.schematron_validated = True
        except subprocess.CalledProcessError as e:
            self.log_error(f'Schematron validation failed to execute: {e}')
            self.schematron_validated = False
        except (etree.XMLSyntaxError, etree.XPathError, Exception) as e:
            self.log_error(f'Failed to parse Schematron output: {e}')
            self.schematron_validated = False

    # ------------------------------------------------------------------
    # MPD parsing helpers
    # ------------------------------------------------------------------

    def extract_base_url(self, root: etree._Element):
        nsmap = root.nsmap.copy()
        if None in nsmap:
            nsmap['ns'] = nsmap.pop(None)
        else:
            nsmap['ns'] = 'urn:mpeg:dash:schema:mpd:2011'

        base_url = root.findtext('.//ns:BaseURL', namespaces=self.nsmap)
        mpd_base = re.sub(r'/(?:[^/]+)$', '/', self.FINAL_URL)
        if not base_url:
            base_url = mpd_base
        elif not base_url.startswith(('http://', 'https://')):
            # Relative BaseURL — resolve against the MPD's own URL
            base_url = urljoin(mpd_base, base_url)
        if not base_url.endswith('/'):
            base_url += '/'
        self.BASE_URL = base_url
        logger.info(f'Extracted BaseURL: {self.BASE_URL}')

    def parse_adaptation_sets(self, tree: etree._ElementTree) -> List[Dict]:
        adaptation_sets = tree.xpath('//ns:AdaptationSet', namespaces=self.nsmap)
        parsed = []
        for as_idx, adaptation_set in enumerate(adaptation_sets, start=1):
            info = {
                'type': adaptation_set.get('contentType', '').lower(),
                'codecs': adaptation_set.get('codecs', ''),
                'lang': adaptation_set.get('lang', ''),
                'mimeType': adaptation_set.get('mimeType', '').lower(),
                'id': adaptation_set.get('id', f'AS_{as_idx}'),
                'ContentProtection': adaptation_set.findall('ns:ContentProtection', namespaces=self.nsmap),
                'Representations': adaptation_set.findall('ns:Representation', namespaces=self.nsmap),
                'Accessibility': adaptation_set.findall('ns:Accessibility', namespaces=self.nsmap),
                'SegmentTemplate': adaptation_set.find('ns:SegmentTemplate', namespaces=self.nsmap),
            }
            if not info['type']:
                if any(c in info['codecs'].lower() for c in ['avc1', 'hvc1', 'hev1']):
                    info['type'] = 'video'
                elif any(c in info['codecs'].lower() for c in ['mp4a', 'ac-3', 'ec-3']):
                    info['type'] = 'audio'
                else:
                    info['type'] = 'unknown'
            parsed.append(info)
        return parsed

    def parse_representations(self, adaptation_set: Dict) -> List[Dict]:
        representations = []
        for rep in adaptation_set['Representations']:
            rep_info = {
                'id': rep.get('id', 'Unknown'),
                'bandwidth': rep.get('bandwidth', 'N/A'),
                'codecs': rep.get('codecs', 'N/A'),
                'lang': rep.get('lang', adaptation_set.get('lang', '')),
                'width': rep.get('width', 'Unknown'),
                'height': rep.get('height', 'Unknown'),
                'frameRate': rep.get('frameRate', 'Unknown'),
                'AudioChannelConfiguration': rep.find('ns:AudioChannelConfiguration', namespaces=self.nsmap),
                'SegmentTemplate': rep.find('ns:SegmentTemplate', namespaces=self.nsmap),
            }
            representations.append(rep_info)
        return representations

    def extract_drm_info(self, content_protections: List[etree._Element]) -> str:
        drm_info = ''
        if content_protections:
            for cp in content_protections:
                scheme_id_uri = cp.get('schemeIdUri', 'Unknown Scheme')
                drm_name = get_drm_name(scheme_id_uri, self.known_uuids, self.drm_names)
                if scheme_id_uri.lower() not in [s.lower() for s in self.drm_schemes]:
                    self.drm_schemes.append(scheme_id_uri)
                    self.drm_scheme_names.append(drm_name)
                drm_info += f' - {drm_name} ({scheme_id_uri})<br>'
                encryption_key_url = cp.get('{http://www.w3.org/2001/XMLSchema-instance}default_KID', '')
                if encryption_key_url:
                    drm_info += f'   Encryption key URL: {encryption_key_url} | DRM Type: {drm_name}<br>'
        else:
            drm_info = ' - None<br>'
        return drm_info.strip()

    def extract_captions_info(self, accessibility_elements: List[etree._Element]) -> str:
        captions_info = ''
        if accessibility_elements:
            captions_info = '<div class="captions-info"><b>Closed Captions:</b><br>'
            for acc in accessibility_elements:
                scheme_id_uri = acc.get('schemeIdUri', 'Unknown Scheme')
                value = acc.get('value', 'Unknown Service')
                captions_info += f' - Scheme: {scheme_id_uri}, Service: {value}<br>'
            captions_info += '</div>'
        return captions_info

    def parse_segment_timeline(self, segment_timeline: etree._Element, timescale: int) -> List[int]:
        segment_times = []
        last_end_time = 0
        for s in segment_timeline.findall('ns:S', namespaces=self.nsmap):
            t = s.get('t')
            d = s.get('d')
            r = s.get('r', '0')
            if d is None:
                continue
            duration = int(d) if is_numeric(d) else 0
            repeat = int(r) if is_numeric(r) else 0
            start_time = int(t) if t and is_numeric(t) else last_end_time
            for _ in range(repeat + 1):
                segment_times.append(start_time)
                start_time += duration
            last_end_time = start_time
        return segment_times

    # ------------------------------------------------------------------
    # Segment validation (pure Python MP4 box parsing — no external tools)
    # ------------------------------------------------------------------

    @staticmethod
    def _read_mp4_boxes(file_path: Path) -> Dict[str, bool]:
        """
        Walk the top-level ISO BMFF boxes in a file and return a dict of
        box-type -> True for every box found.  Handles both 32-bit and
        64-bit (extended) size fields.
        """
        boxes: Dict[str, bool] = {}
        try:
            with open(file_path, 'rb') as f:
                while True:
                    header = f.read(8)
                    if len(header) < 8:
                        break
                    size = int.from_bytes(header[:4], 'big')
                    box_type = header[4:8].decode('latin-1')
                    boxes[box_type] = True
                    if size == 1:
                        # 64-bit extended size
                        ext = f.read(8)
                        if len(ext) < 8:
                            break
                        size = int.from_bytes(ext, 'big')
                        payload = size - 16
                    elif size == 0:
                        # box extends to EOF
                        break
                    else:
                        payload = size - 8
                    if payload > 0:
                        f.seek(payload, 1)
        except (OSError, ValueError):
            pass
        return boxes

    @staticmethod
    def _read_ftyp_brand(file_path: Path) -> str:
        """Return the major brand string from the ftyp box, or 'Unknown'."""
        try:
            with open(file_path, 'rb') as f:
                header = f.read(8)
                if len(header) < 8:
                    return 'Unknown'
                box_type = header[4:8].decode('latin-1')
                if box_type == 'ftyp':
                    brand = f.read(4)
                    return brand.decode('latin-1').strip() if len(brand) == 4 else 'Unknown'
        except (OSError, ValueError):
            pass
        return 'Unknown'

    def validate_init_segment(self, segment_file: Path) -> Tuple[str, str]:
        if not segment_file.is_file():
            return '❌ Segment file not found.', 'Unknown'

        major_brand = self._read_ftyp_brand(segment_file).lower()
        boxes = self._read_mp4_boxes(segment_file)

        cmaf_brands = {'cmfc', 'cmfs', 'cmfa', 'cmfv'}
        fmp4_brands = {'isom', 'iso6', 'avc1', 'mp41', 'mp42'}

        if major_brand in cmaf_brands:
            stream_type = 'CMAF Compliant Initialization Segment'
        elif major_brand in fmp4_brands:
            if 'ftyp' in boxes and 'moov' in boxes and 'moof' not in boxes:
                stream_type = 'Fragmented MP4 Initialization Segment'
            else:
                stream_type = 'Unknown Initialization Segment Type'
        else:
            stream_type = 'Unknown Stream Type'

        return f'Stream Type: {stream_type} (Major brand: {major_brand})', stream_type

    def validate_media_segment(self, segment_file: Path) -> str:
        if not segment_file.is_file():
            return 'Segment file not found.'
        boxes = self._read_mp4_boxes(segment_file)
        if 'moof' in boxes and 'mdat' in boxes:
            return 'Fragmented MP4 Segment'
        return 'Unknown Segment Type'

    def download_and_validate_segment(self, url: str, segment_path: Path) -> Tuple[Optional[int], str]:
        status_code, _ = download_file(url, segment_path)
        if status_code == 200 and segment_path.is_file() and segment_path.stat().st_size > 0:
            segment_type = self.validate_media_segment(segment_path)
            return status_code, segment_type
        elif status_code == 404:
            return status_code, 'Segment Not Found'
        elif status_code:
            return status_code, f'Failed to download segment ({status_code})'
        else:
            return None, 'Failed to download segment'

    # ------------------------------------------------------------------
    # Core parse + validate loop
    # ------------------------------------------------------------------

    def parse_and_validate_segments(self, tree: etree._ElementTree):
        adaptation_sets = self.parse_adaptation_sets(tree)
        if not adaptation_sets:
            self.log_error('No AdaptationSets found in the MPD file.')
            return

        for adaptation_set in adaptation_sets:
            adaptation_type = adaptation_set['type'] or 'unknown'
            adaptation_entry = f'<h2>Adaptation Set: {adaptation_type.capitalize()}</h2>'

            drm_info_html = self.extract_drm_info(adaptation_set['ContentProtection'])
            adaptation_entry += f'<div class="drm-info"><b>DRM Information:</b><br>{drm_info_html}</div>'

            captions_info = self.extract_captions_info(adaptation_set['Accessibility'])
            if captions_info:
                adaptation_entry += captions_info

            representations = self.parse_representations(adaptation_set)
            if not representations:
                logger.info(f"No Representations in AdaptationSet ID: {adaptation_set['id']}")
                continue

            if adaptation_type == 'audio':
                representation_container = '<div class="audio-representations">\n'
            elif adaptation_type == 'video':
                representation_container = '<div class="video-representations">\n'
            else:
                representation_container = '<div class="unknown-representations">\n'

            for representation in representations:
                rep_id = representation['id']
                bandwidth = representation['bandwidth']
                codecs = representation['codecs']
                lang = representation['lang'] or 'N/A'

                rep_entry = f'<h3>Representation ID: {rep_id}</h3>'
                if self.closed_captions_detected:
                    rep_entry += '<b>Closed Captions:</b> Available<br>'

                rep_entry += '<div class="representation-details">'
                rep_entry += f'<b>Bandwidth:</b> {bandwidth}<br>'
                rep_entry += f'<b>Codec:</b> {codecs}<br>'
                codec_translation = get_codec_translation(codecs)
                rep_entry += f'<b>Codec Profile:</b> {codec_translation}<br>'

                # CSV fields initialised to safe defaults before type-specific branches
                resolution = 'N/A'
                frame_rate = 'N/A'
                audio_channel = 'N/A'

                if adaptation_type == 'audio':
                    audio_channel = 'Unknown'
                    if representation['AudioChannelConfiguration'] is not None:
                        audio_channel = representation['AudioChannelConfiguration'].get('value', 'Unknown')
                        if not is_numeric(audio_channel):
                            audio_channel = 'Unknown'
                    rep_entry += f'<b>Language:</b> {lang}<br>'
                    rep_entry += f'<b>Channels:</b> {audio_channel}<br>'

                elif adaptation_type == 'video':
                    width = representation['width']
                    height = representation['height']
                    frame_rate = representation['frameRate']
                    resolution = f'{width}x{height}' if is_numeric(width) and is_numeric(height) else 'Unknown'
                    if is_numeric(frame_rate):
                        resolution += f' @ {frame_rate} fps'
                    else:
                        resolution += ' @ Unknown fps'
                    rep_entry += f'<b>Resolution:</b> {resolution}<br>'

                rep_entry += '</div>'

                segment_template = adaptation_set['SegmentTemplate']
                if not segment_template and representation['SegmentTemplate'] is not None:
                    segment_template = representation['SegmentTemplate']

                segment_results = []  # (idx, url, status, seg_type)

                if segment_template is not None:
                    media_template = segment_template.get('media', '')
                    init_template = segment_template.get('initialization', '')
                    timescale_str = segment_template.get('timescale', '1')
                    timescale = int(timescale_str) if is_numeric(timescale_str) else 1

                    segment_timeline = segment_template.find('ns:SegmentTimeline', namespaces=self.nsmap)
                    if segment_timeline is not None:
                        segment_times = self.parse_segment_timeline(segment_timeline, timescale)
                    else:
                        duration_str = segment_template.get('duration', '0')
                        duration = int(duration_str) / timescale if is_numeric(duration_str) else 0
                        segment_times = [int(i * duration) for i in range(1, self.num_segments + 1)]

                    # Initialization segment
                    if init_template:
                        init_url = (init_template
                                    .replace('$RepresentationID$', rep_id)
                                    .replace('$Bandwidth$', bandwidth))
                        full_init_url = urljoin(self.BASE_URL, init_url)
                        init_segment_path = Path(self.temp_dir.name) / f'init_{rep_id}.mp4'

                        logger.info(f'Downloading Initialization Segment: {full_init_url}')
                        init_status_code, _ = download_file(full_init_url, init_segment_path)
                        if init_status_code == 200 and init_segment_path.is_file() and init_segment_path.stat().st_size > 0:
                            init_validation_output, init_stream_type = self.validate_init_segment(init_segment_path)
                            if 'CMAF' in init_stream_type:
                                init_validation_output = f'<span class="cmaf">{init_validation_output}</span>'
                            elif 'Fragmented' in init_stream_type:
                                init_validation_output = f'<span class="fragmented">{init_validation_output}</span>'
                            else:
                                init_validation_output = f'<span class="error">{init_validation_output}</span>'
                            rep_entry += (
                                '<div class="segment-info">'
                                '<b>Initialization Segment:</b><br>'
                                f'<a href="{full_init_url}">{full_init_url}</a><br>'
                                f'{init_validation_output}'
                                '</div>'
                            )
                        else:
                            formatted_status = format_http_status(init_status_code)
                            rep_entry += (
                                '<div class="segment-info">'
                                '<b>Initialization Segment:</b><br>'
                                f'<a href="{full_init_url}">{full_init_url}</a><br>'
                                f'<span class="error">Failed to download initialization segment (HTTP {formatted_status})</span>'
                                '</div>'
                            )
                    else:
                        rep_entry += '<p>Initialization template not found.</p>'

                    # Media segments
                    media_template = (media_template
                                      .replace('$RepresentationID$', rep_id)
                                      .replace('$Bandwidth$', bandwidth))
                    segment_urls = []
                    for i, t in enumerate(segment_times[:self.num_segments], start=1):
                        url = (media_template
                               .replace('$Time$', str(int(t)))
                               .replace('$Number$', str(i)))
                        segment_urls.append(urljoin(self.BASE_URL, url))

                    with ThreadPoolExecutor(max_workers=10) as executor:
                        future_to_url = {
                            executor.submit(
                                self.download_and_validate_segment,
                                url,
                                Path(self.temp_dir.name) / f'segment_{rep_id}_{i}.mp4'
                            ): (i, url)
                            for i, url in enumerate(segment_urls, start=1)
                        }
                        for future in as_completed(future_to_url):
                            idx, seg_url = future_to_url[future]
                            try:
                                status, seg_type = future.result()
                                segment_results.append((idx, seg_url, status, seg_type))
                            except Exception as e:
                                logger.error(f'Exception while downloading segment {seg_url}: {e}')
                                segment_results.append((idx, seg_url, None, '❌ Exception occurred'))

                    # Build segment table HTML
                    segment_table = (
                        '<table class="segment-table">'
                        '<thead><tr>'
                        '<th>No.</th><th>Segment URL</th>'
                        '<th>HTTP Status</th><th>Segment Type</th>'
                        '</tr></thead><tbody>'
                    )
                    for idx, seg_url, status, seg_type in sorted(segment_results, key=lambda x: x[0]):
                        formatted_status = format_http_status(status)
                        if seg_type == 'Fragmented MP4 Segment':
                            seg_type_fmt = f'<span class="fragmented">{seg_type}</span>'
                        elif 'CMAF' in seg_type or 'Initialization' in seg_type:
                            seg_type_fmt = f'<span class="cmaf">{seg_type}</span>'
                        else:
                            seg_type_fmt = f'<span class="error">{seg_type}</span>'
                        segment_table += (
                            f'<tr>'
                            f'<td>{idx}</td>'
                            f'<td><a href="{seg_url}">{seg_url}</a></td>'
                            f'<td>{formatted_status}</td>'
                            f'<td>{seg_type_fmt}</td>'
                            f'</tr>'
                        )
                    segment_table += '</tbody></table>'
                    rep_entry += segment_table
                else:
                    rep_entry += '<p>SegmentTemplate not found.</p>'

                representation_container += f'{rep_entry}\n'

                # ----------------------------------------------------------
                # Collect CSV row for this representation
                # ----------------------------------------------------------
                ok_count = sum(1 for _, _, s, _ in segment_results if s == 200)
                fail_count = len(segment_results) - ok_count
                drm_summary = '; '.join(self.drm_scheme_names) if self.drm_scheme_names else 'None'

                self.csv_rows.append({
                    'Stream Type': adaptation_type.capitalize(),
                    'Rep ID': rep_id,
                    'Bandwidth (bps)': bandwidth,
                    'Codec': codecs,
                    'Codec Profile': codec_translation,
                    'Resolution': resolution,
                    'Frame Rate': frame_rate,
                    'Language': lang if adaptation_type == 'audio' else 'N/A',
                    'Channels': audio_channel if adaptation_type == 'audio' else 'N/A',
                    'DRM': drm_summary,
                    'Segments Checked': len(segment_results),
                    'Segments OK': ok_count,
                    'Segments Failed': fail_count,
                })

            representation_container += '</div>\n'
            adaptation_entry += representation_container
            self.adaptation_set_data.append(adaptation_entry)

    # ------------------------------------------------------------------
    # HTML report
    # ------------------------------------------------------------------

    def generate_html_report(self):
        with open(self.report_file, 'w', encoding='utf-8') as report:
            report.write('<html>\n<head>\n')
            report.write('    <title>DASH Validation Report</title>\n')
            report.write('    <style>\n')
            report.write('        body { font-family: Arial, sans-serif; margin: 20px; }\n')
            report.write('        .header { font-size: 1.5em; margin-bottom: 20px; }\n')
            report.write('        .drm-info, .captions-info, .segment-info { margin-bottom: 20px; }\n')
            report.write('        .segment-table { width: 100%; border-collapse: collapse; }\n')
            report.write('        .segment-table th, .segment-table td { border: 1px solid #ddd; padding: 8px; }\n')
            report.write('        .segment-table th { background-color: #f2f2f2; }\n')
            report.write('        .cmaf { color: green; }\n')
            report.write('        .fragmented { color: blue; }\n')
            report.write('        .progressive { color: purple; }\n')
            report.write('        .error { color: red; }\n')
            report.write('        .status-ok { color: green; font-weight: bold; }\n')
            report.write('        .status-error { color: red; font-weight: bold; }\n')
            report.write('        a { color: #1a0dab; text-decoration: none; }\n')
            report.write('        a:hover { text-decoration: underline; }\n')
            report.write('    </style>\n</head>\n<body>\n')

            report.write('<h1 class="header">DASH Validation Report</h1>\n')
            report.write(f'<p style="margin-top: 0;">Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>\n')
            formatted_status = format_http_status(self.mpd_http_status)
            report.write(
                f'<p><strong>URL:</strong> <a href="{self.mpd_url}">{self.mpd_url}</a> '
                f'<strong>| HTTP Status</strong> - {formatted_status}</p>\n'
            )

            if self.error_messages:
                report.write('<h2>Errors Encountered</h2>\n<ul class="error-list">\n')
                for error in self.error_messages:
                    report.write(f'<li class="error">{error}</li>\n')
                report.write('</ul>\n')

            for adaptation_set_html in self.adaptation_set_data:
                report.write(f'{adaptation_set_html}\n')

            if self.closed_captions_detected:
                report.write('<div class="captions-info"><b>Closed Captions:</b> Available</div>\n')

            report.write('</body>\n</html>\n')

    # ------------------------------------------------------------------
    # CSV report
    # ------------------------------------------------------------------

    def generate_csv_report(self, csv_file: str):
        """Write a flat CSV summary — one row per DASH Representation."""
        fieldnames = [
            'Stream Type',
            'Rep ID',
            'Bandwidth (bps)',
            'Codec',
            'Codec Profile',
            'Resolution',
            'Frame Rate',
            'Language',
            'Channels',
            'DRM',
            'Segments Checked',
            'Segments OK',
            'Segments Failed',
        ]
        try:
            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.csv_rows)
            logger.info(f"CSV report written to '{csv_file}'.")
        except Exception as e:
            logger.error(f'Failed to write CSV report: {e}')

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self):
        try:
            logger.info('Starting dependency check...')
            self.check_dependencies()
            if not self.dependencies_ok:
                self.log_error('Some dependencies missing. Continuing with limited functionality.')

            logger.info('Downloading MPD...')
            self.download_mpd()
            if not self.mpd_downloaded:
                self.log_error('MPD download failed.')
            else:
                manifest_path = Path(self.temp_dir.name) / 'manifest.mpd'
                try:
                    tree = etree.parse(str(manifest_path))
                    root = tree.getroot()
                except etree.XMLSyntaxError as e:
                    self.log_error(f'Failed to parse MPD XML: {e}')
                    tree = None
                    root = None

                logger.info('Validating MPD with Schematron...')
                self.validate_mpd_schematron()
                if not self.schematron_validated:
                    self.log_error('Schematron validation failed. Continuing without it.')

                logger.info('Extracting BaseURL...')
                if root is not None:
                    self.extract_base_url(root)
                else:
                    self.log_error('Cannot extract BaseURL — root element is None.')

                if tree is not None:
                    logger.info('Parsing and validating segments...')
                    self.parse_and_validate_segments(tree)
                else:
                    self.log_error('Cannot parse segments — tree is None.')

            logger.info('Generating HTML report...')
            self.generate_html_report()

            if self.error_messages:
                logger.info(f'Validation completed with {len(self.error_messages)} error(s). Check the report.')
            else:
                logger.info('Validation completed successfully.')

        finally:
            self.temp_dir.cleanup()
            logger.info(f'Temporary directory cleaned up.')


# ---------------------------------------------------------------------------
# CLI entry point (direct usage)
# ---------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Validate a DASH MPD and generate HTML and CSV reports.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('MPD_URL', type=str, help='URL of the MPD file')
    parser.add_argument('num_segments', type=int, help='Number of segments to validate')
    parser.add_argument('REPORT_FILE', type=str, help='Path to the output HTML report file (.html)')
    args = parser.parse_args()
    if not args.REPORT_FILE.lower().endswith('.html'):
        parser.error('The report file must have an .html extension.')
    return args


def main():
    args = parse_arguments()
    validator = DashValidator(args.MPD_URL, args.num_segments, args.REPORT_FILE)
    validator.run()
    csv_file = re.sub(r'\.html$', '.csv', args.REPORT_FILE, flags=re.IGNORECASE)
    validator.generate_csv_report(csv_file)


if __name__ == '__main__':
    main()

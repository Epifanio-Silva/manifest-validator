#!/usr/bin/env python3

"""
validate_hls.py

Validates an HLS master playlist (.m3u8): fetches all variant streams
(video, audio, subtitle), checks segment URLs, detects DRM and codec
information, and generates an HTML report and a CSV summary.

Usage (direct):
    python validate_hls.py <HLS_URL> <num_segments> <REPORT_FILE.html>

Usage (via validate.py):
    python validate.py <HLS_URL> --segments <N>
"""

import argparse
import csv
import logging
import os
import re
import sys
from datetime import datetime
from urllib.parse import urljoin, urlparse
from typing import Tuple, Dict, Any, List

import requests
import m3u8

import codec_profile

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DRM_MAPPING = {
    'urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed': 'Widevine',
    'urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95': 'PlayReady',
    'com.apple.streamingkeydelivery': 'FairPlay',
    'urn:uuid:00000000-0000-0000-0000-000000000000': 'ClearKey',
    'CAB/keyfile': 'Verimatrix',
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; ManifestValidator/1.0)',
    'Accept': '*/*',
}

session = requests.Session()
session.headers.update(HEADERS)


# ---------------------------------------------------------------------------
# Codec helpers
# ---------------------------------------------------------------------------

def parse_codecs(codecs_string: str) -> Tuple[Tuple[str, str], Tuple[str, str]]:
    """Separate audio and video codecs and return (codec, translation) tuples."""
    audio_codec = video_codec = 'N/A'
    audio_translation = video_translation = 'N/A'
    if isinstance(codecs_string, str) and codecs_string:
        for codec in codecs_string.split(','):
            codec = codec.strip()
            translation = codec_profile.parse_codec(codec)
            if codec.startswith(('mp4a', 'ac-3', 'ec-3')):
                audio_codec = codec
                audio_translation = translation
            else:
                video_codec = codec
                video_translation = translation
    return (audio_codec, audio_translation), (video_codec, video_translation)


def get_codecs_from_iframe(line: str) -> str:
    match = re.search(r'CODECS="([^"]+)"', line)
    return match.group(1) if match else 'N/A'


def extract_codecs_from_media_playlist(playlist) -> str:
    for line in playlist.dumps().splitlines():
        if line.startswith('#EXT-X-MEDIA:'):
            match = re.search(r'TYPE=AUDIO.*?CODECS="([^"]+)"', line)
            if match:
                return match.group(1)
        elif line.startswith('#EXT-X-STREAM-INF:'):
            match = re.search(r'CODECS="([^"]+)"', line)
            if match:
                return match.group(1)
    return 'N/A'


# ---------------------------------------------------------------------------
# DRM helpers
# ---------------------------------------------------------------------------

def get_drm_type(keyformat, key_uri: str) -> str:
    if (keyformat is None or keyformat == '') and 'CAB/keyfile' in key_uri:
        return 'Verimatrix'
    return DRM_MAPPING.get(keyformat, 'Unknown DRM')


# ---------------------------------------------------------------------------
# Segment / URL helpers
# ---------------------------------------------------------------------------

def get_file_extension(uri: str) -> str:
    path = urlparse(uri).path
    return os.path.splitext(path)[1].lower()


def check_url_status(url: str) -> Any:
    try:
        response = session.head(url, timeout=5, allow_redirects=True)
        return response.status_code
    except requests.RequestException:
        return 'Request Failed'


def determine_segment_type(playlist, playlist_text: str) -> str:
    if '#EXT-X-MAP' in playlist_text:
        return 'fMP4'
    if playlist.segments:
        ext = get_file_extension(playlist.segments[0].uri)
        if ext in ('.ts', '.tsv', '.tsa'):
            return 'MPEG-TS'
        elif ext in ('.mp4', '.m4s', '.cmf', '.cmfv', '.cmfa'):
            return 'fMP4'
        elif ext == '.vtt':
            return 'WebVTT'
    return 'Unknown'


def is_cmaf_compliant(playlist, playlist_text: str) -> bool:
    if determine_segment_type(playlist, playlist_text) != 'fMP4':
        return False
    if '#EXT-X-MAP' not in playlist_text:
        return False
    durations = [s.duration for s in playlist.segments]
    if not durations:
        return False
    avg = sum(durations) / len(durations)
    return all(abs(d - avg) < 0.1 for d in durations)


# ---------------------------------------------------------------------------
# Playlist segment parsing
# ---------------------------------------------------------------------------

def parse_and_validate_segments(
    playlist_url: str,
    segment_count: int,
    codecs_info: Dict,
    media_type: str,
    additional_info: Dict,
    base_uri: str,
) -> Dict:
    data = {
        'Playlist URL': playlist_url,
        'HTTP Status': '',
        'Bandwidth': codecs_info.get('bandwidth', 'N/A'),
        'Audio Codec': 'N/A',
        'Audio Codec Translation': 'N/A',
        'Video Codec': 'N/A',
        'Video Codec Translation': 'N/A',
        'Segment Type': 'Unknown',
        'CMAF Compliant': 'No',
        'DRM Info': [],
        'Segments': [],
    }

    try:
        response = session.get(playlist_url, allow_redirects=True)
        data['HTTP Status'] = response.status_code
        if response.status_code != 200:
            logger.error(f'Failed to fetch playlist: {response.status_code} for {playlist_url}')
            return data
    except requests.RequestException as e:
        logger.error(f'Request failed for {playlist_url}: {e}')
        data['HTTP Status'] = 'Request Failed'
        return data

    current_base_uri = response.url.rsplit('/', 1)[0] + '/'
    playlist = m3u8.loads(response.text)

    (audio_codec, audio_translation), (video_codec, video_translation) = parse_codecs(codecs_info.get('codecs', ''))
    data['Audio Codec'] = audio_codec
    data['Audio Codec Translation'] = audio_translation
    data['Video Codec'] = video_codec
    data['Video Codec Translation'] = video_translation
    data['Segment Type'] = determine_segment_type(playlist, response.text)
    data['CMAF Compliant'] = 'Yes' if is_cmaf_compliant(playlist, response.text) else 'No'

    for key in playlist.keys:
        if key and key.uri:
            key_url = urljoin(current_base_uri, key.uri)
            drm_type = get_drm_type(key.keyformat, key.uri)
            data['DRM Info'].append({'Key URL': key_url, 'DRM Type': drm_type})

    for i, segment in enumerate(playlist.segments[:segment_count]):
        segment_url = urljoin(current_base_uri, segment.uri)
        status = check_url_status(segment_url)
        data['Segments'].append({'No.': i + 1, 'URL': segment_url, 'Status': status})

    return data


# ---------------------------------------------------------------------------
# Master playlist fetcher
# ---------------------------------------------------------------------------

def get_segments_and_keys_from_hls_url(hls_url: str, segment_count: int) -> Dict:
    report_data: Dict = {
        'HLS URL': hls_url,
        'HTTP Status': '',
        'Segments Requested': segment_count,
        'Variant Streams': [],
        'Closed Captions': [],
        'I-Frame Playlists': [],
        'Variant Summary': '',
    }

    try:
        response = session.get(hls_url, allow_redirects=True)
        report_data['HTTP Status'] = response.status_code
        if response.status_code != 200:
            report_data['Error'] = f'Failed to fetch HLS playlist: {response.status_code}'
            return report_data
    except requests.RequestException as e:
        report_data['HTTP Status'] = 'Request Failed'
        report_data['Error'] = f'Request failed: {e}'
        return report_data

    base_uri = response.url.rsplit('/', 1)[0] + '/'
    playlist = m3u8.loads(response.text)

    audio_renditions: Dict[str, Dict] = {}
    subtitle_renditions: Dict[str, Dict] = {}

    for media in playlist.media:
        if media.type == 'AUDIO':
            key = f'{media.group_id}_{media.language}_{media.name}'
            audio_renditions[key] = {
                'uri': media.uri,
                'group_id': media.group_id,
                'language': media.language,
                'name': media.name,
                'default': media.default,
                'autoselect': media.autoselect,
                'channels': media.channels,
            }
        elif media.type == 'SUBTITLES':
            key = f'{media.group_id}_{media.language}_{media.name}'
            subtitle_renditions[key] = {
                'uri': media.uri,
                'group_id': media.group_id,
                'language': media.language,
                'name': media.name,
            }
        elif media.type == 'CLOSED-CAPTIONS':
            report_data['Closed Captions'].append({
                'Group ID': media.group_id,
                'Name': media.name,
                'Instream ID': media.instream_id,
            })

    # Audio renditions
    for rendition_info in audio_renditions.values():
        audio_url = urljoin(base_uri, rendition_info['uri']) if rendition_info['uri'] else 'N/A'
        if rendition_info['uri']:
            codecs_info = {'codecs': 'N/A', 'bandwidth': 'N/A'}
            for variant in playlist.playlists:
                if variant.stream_info and variant.stream_info.audio == rendition_info['group_id']:
                    codecs = variant.stream_info.codecs
                    bandwidth = variant.stream_info.bandwidth
                    (audio_codec, _), _ = parse_codecs(codecs)
                    if audio_codec != 'N/A':
                        codecs_info['codecs'] = audio_codec
                        codecs_info['bandwidth'] = str(int(int(bandwidth) * 0.1))
                        break
            if codecs_info['codecs'] == 'N/A':
                try:
                    r = session.get(audio_url, allow_redirects=True)
                    if r.status_code == 200:
                        ap = m3u8.loads(r.text)
                        codecs_info['codecs'] = extract_codecs_from_media_playlist(ap)
                except requests.RequestException:
                    pass

            additional_info = {
                'Language': rendition_info.get('language', 'N/A'),
                'Name': rendition_info.get('name', 'N/A'),
                'Channels': rendition_info.get('channels', 'N/A'),
                'Default': 'YES' if rendition_info.get('default', False) else 'NO',
                'Autoselect': 'YES' if rendition_info.get('autoselect', False) else 'NO',
            }
            audio_data = parse_and_validate_segments(
                playlist_url=audio_url,
                segment_count=segment_count,
                codecs_info=codecs_info,
                media_type='Audio',
                additional_info=additional_info,
                base_uri=base_uri,
            )
            report_data['Variant Streams'].append({
                'Type': 'Audio',
                'Info': rendition_info,
                'Data': audio_data,
                'Additional Info': additional_info,
            })
        else:
            report_data['Variant Streams'].append({
                'Type': 'Audio',
                'Info': rendition_info,
                'Data': None,
                'Additional Info': None,
            })

    # Subtitle renditions
    for rendition_info in subtitle_renditions.values():
        subtitle_url = urljoin(base_uri, rendition_info['uri']) if rendition_info['uri'] else 'N/A'
        if rendition_info['uri']:
            additional_info = {
                'Language': rendition_info.get('language', 'N/A'),
                'Name': rendition_info.get('name', 'N/A'),
            }
            subtitle_data = parse_and_validate_segments(
                playlist_url=subtitle_url,
                segment_count=segment_count,
                codecs_info={'codecs': 'N/A', 'bandwidth': 'N/A'},
                media_type='Subtitle',
                additional_info=additional_info,
                base_uri=base_uri,
            )
            report_data['Variant Streams'].append({
                'Type': 'Subtitle',
                'Info': rendition_info,
                'Data': subtitle_data,
                'Additional Info': additional_info,
            })
        else:
            report_data['Variant Streams'].append({
                'Type': 'Subtitle',
                'Info': rendition_info,
                'Data': None,
                'Additional Info': None,
            })

    # Video variant streams
    if playlist.is_variant:
        for variant in playlist.playlists:
            variant_url = urljoin(base_uri, variant.uri)
            codecs = variant.stream_info.codecs if variant.stream_info and variant.stream_info.codecs else 'N/A'
            bandwidth = variant.stream_info.bandwidth if variant.stream_info and variant.stream_info.bandwidth else 'N/A'
            if bandwidth != 'N/A':
                bandwidth = str(int(float(bandwidth)))
            codecs_info = {'codecs': codecs, 'bandwidth': bandwidth}
            additional_info = {
                'Resolution': (
                    f'{variant.stream_info.resolution[0]}x{variant.stream_info.resolution[1]}'
                    if variant.stream_info and variant.stream_info.resolution else 'N/A'
                ),
                'Frame Rate': (
                    variant.stream_info.frame_rate
                    if variant.stream_info and variant.stream_info.frame_rate else 'N/A'
                ),
                'Average Bandwidth': (
                    str(int(variant.stream_info.average_bandwidth))
                    if variant.stream_info and variant.stream_info.average_bandwidth else 'N/A'
                ),
            }
            video_data = parse_and_validate_segments(
                playlist_url=variant_url,
                segment_count=segment_count,
                codecs_info=codecs_info,
                media_type='Video',
                additional_info=additional_info,
                base_uri=base_uri,
            )
            report_data['Variant Streams'].append({
                'Type': 'Video',
                'Info': variant.stream_info,
                'Data': video_data,
                'Additional Info': additional_info,
            })

    # I-frame playlists
    for line in response.text.splitlines():
        if line.startswith('#EXT-X-I-FRAME-STREAM-INF'):
            iframe_url_match = re.search(r'URI="([^"]+)"', line)
            iframe_url = urljoin(base_uri, iframe_url_match.group(1)) if iframe_url_match else 'N/A'
            bandwidth_match = re.search(r'BANDWIDTH=(\d+)', line)
            resolution_match = re.search(r'RESOLUTION=(\d+x\d+)', line)
            report_data['I-Frame Playlists'].append({
                'I-Frame URL': iframe_url,
                'HTTP Status': check_url_status(iframe_url),
                'Codecs': get_codecs_from_iframe(line),
                'Bandwidth': bandwidth_match.group(1) if bandwidth_match else 'N/A',
                'Resolution': resolution_match.group(1) if resolution_match else 'N/A',
            })

    report_data = process_variant_streams(report_data)
    return report_data


def process_variant_streams(report_data: Dict) -> Dict:
    counts = {
        'audio': 0, 'video': 0, 'subtitle': 0,
        'cc': len(report_data.get('Closed Captions', [])),
        'iframe': len(report_data.get('I-Frame Playlists', [])),
    }
    for stream in report_data.get('Variant Streams', []):
        t = stream.get('Type', '').lower()
        if t in counts:
            counts[t] += 1

    summary_parts = []
    if counts['audio']:
        summary_parts.append(f"Audio={counts['audio']}")
    if counts['video']:
        summary_parts.append(f"Video={counts['video']}")
    if counts['subtitle']:
        summary_parts.append(f"Subtitles={counts['subtitle']}")
    if counts['cc']:
        summary_parts.append(f"Closed Captions={counts['cc']}")
    if counts['iframe']:
        summary_parts.append(f"iFrame={counts['iframe']}")
    report_data['Variant Summary'] = '\n'.join(summary_parts)
    return report_data


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def generate_html_report(report_data: Dict, output_file: str):
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>HLS Validation Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .section {{ margin-bottom: 40px; }}
        .drm-info {{ background-color: #fff8e1; padding: 10px; border-radius: 5px; margin-bottom: 20px; }}
        .additional-info {{ background-color: #eef9ff; padding: 10px; border-radius: 5px; margin-bottom: 20px; }}
        .variant-summary {{ background-color: #f5f5f5; padding: 10px; margin: 10px 0; border-radius: 4px; }}
        .variant-summary h3 {{ margin-top: 0; color: #333; }}
        .variant-summary p {{ white-space: pre-line; margin: 0; }}
        .segments {{ margin-bottom: 20px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
        .success {{ color: green; font-weight: bold; }}
        .error {{ color: red; font-weight: bold; }}
        a {{ color: #1a0dab; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <h2 style="margin-bottom: 0;">HLS Validation Report</h2>
    <p style="margin-top: 0;">Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
"""

    hls_status = report_data.get('HTTP Status', 'N/A')
    try:
        status_class = 'success' if int(hls_status) == 200 else 'error'
    except (ValueError, TypeError):
        status_class = 'error'

    html += (
        f'    <strong>URL:</strong> <a href="{report_data.get("HLS URL", "N/A")}">'
        f'{report_data.get("HLS URL", "N/A")}</a> '
        f'<strong>| HTTP Status:</strong> <span class="{status_class}">{hls_status}</span><br>\n'
    )

    if 'Error' in report_data:
        html += f'    <div class="error"><strong>Error:</strong> {report_data["Error"]}</div>\n'

    html += (
        '    <div class="variant-summary">'
        '<h3 style="margin-bottom:5px;">Variant Summary</h3>'
        f'<p>{report_data.get("Variant Summary", "N/A")}</p>'
        '</div>\n'
    )

    for stream in report_data.get('Variant Streams', []):
        adapt_type = stream.get('Type', 'N/A')
        data = stream.get('Data') or {}
        additional_info = stream.get('Additional Info') or {}

        http_status = data.get('HTTP Status', 'N/A')
        try:
            status_class = 'success' if int(http_status) == 200 else 'error'
        except (ValueError, TypeError):
            status_class = 'error'

        html += f'    <div class="section"><h2>{adapt_type} Variant Stream</h2>\n'
        html += '    <div class="additional-info">\n'

        if adapt_type == 'Audio':
            html += (
                f'        <strong>Playlist URL:</strong> <a href="{data.get("Playlist URL","#")}">'
                f'{data.get("Playlist URL","N/A")}</a><br>\n'
                f'        <strong>HTTP Status:</strong> <span class="{status_class}">{http_status}</span><br>\n'
                f'        <strong>Bandwidth:</strong> {data.get("Bandwidth","N/A")}<br>\n'
                f'        <strong>Audio Codec:</strong> {data.get("Audio Codec","N/A")}<br>\n'
                f'        <strong>Audio Codec Profile:</strong> {data.get("Audio Codec Translation","N/A")}<br>\n'
                f'        <strong>Language:</strong> {additional_info.get("Language","N/A")}<br>\n'
                f'        <strong>Name:</strong> {additional_info.get("Name","N/A")}<br>\n'
                f'        <strong>Channels:</strong> {additional_info.get("Channels","N/A")}<br>\n'
                f'        <strong>Default:</strong> {additional_info.get("Default","N/A")}<br>\n'
                f'        <strong>Autoselect:</strong> {additional_info.get("Autoselect","N/A")}<br>\n'
                f'        <strong>Segment Type:</strong> {data.get("Segment Type","N/A")}<br>\n'
                f'        <strong>CMAF Compliant:</strong> {data.get("CMAF Compliant","N/A")}\n'
            )
        elif adapt_type == 'Video':
            html += (
                f'        <strong>Playlist URL:</strong> <a href="{data.get("Playlist URL","#")}">'
                f'{data.get("Playlist URL","N/A")}</a><br>\n'
                f'        <strong>HTTP Status:</strong> <span class="{status_class}">{http_status}</span><br>\n'
                f'        <strong>Bandwidth:</strong> {data.get("Bandwidth","N/A")}<br>\n'
                f'        <strong>Video Codec:</strong> {data.get("Video Codec","N/A")}<br>\n'
                f'        <strong>Video Codec Profile:</strong> {data.get("Video Codec Translation","N/A")}<br>\n'
                f'        <strong>Resolution:</strong> {additional_info.get("Resolution","N/A")}<br>\n'
                f'        <strong>Frame Rate:</strong> {additional_info.get("Frame Rate","N/A")}<br>\n'
                f'        <strong>Avg Bandwidth:</strong> {additional_info.get("Average Bandwidth","N/A")}<br>\n'
                f'        <strong>Segment Type:</strong> {data.get("Segment Type","N/A")}<br>\n'
                f'        <strong>CMAF Compliant:</strong> {data.get("CMAF Compliant","N/A")}\n'
            )
        elif adapt_type == 'Subtitle':
            html += (
                f'        <strong>Playlist URL:</strong> <a href="{data.get("Playlist URL","#")}">'
                f'{data.get("Playlist URL","N/A")}</a><br>\n'
                f'        <strong>HTTP Status:</strong> <span class="{status_class}">{http_status}</span><br>\n'
                f'        <strong>Language:</strong> {additional_info.get("Language","N/A")}<br>\n'
                f'        <strong>Name:</strong> {additional_info.get("Name","N/A")}<br>\n'
                f'        <strong>Segment Type:</strong> {data.get("Segment Type","N/A")}<br>\n'
                f'        <strong>CMAF Compliant:</strong> {data.get("CMAF Compliant","N/A")}\n'
            )

        html += '    </div>\n'

        if data:
            drm_info = data.get('DRM Info', [])
            if drm_info:
                html += '    <div class="drm-info"><strong>DRM Information:</strong><ul>\n'
                for drm in drm_info:
                    html += (
                        f'        <li>Key URL: <a href="{drm.get("Key URL","#")}">'
                        f'{drm.get("Key URL","N/A")}</a> | DRM Type: {drm.get("DRM Type","N/A")}</li>\n'
                    )
                html += '    </ul></div>\n'
            else:
                html += '    <div class="drm-info"><strong>DRM Information:</strong> None</div>\n'

            segments = data.get('Segments', [])
            if segments:
                html += '    <div class="segments"><h3>Segments</h3>\n    <table><thead><tr><th>No.</th><th>Segment URL</th><th>HTTP Status</th></tr></thead><tbody>\n'
                for seg in segments:
                    seg_status = seg.get('Status', 'N/A')
                    try:
                        seg_class = 'success' if int(seg_status) == 200 else 'error'
                    except (ValueError, TypeError):
                        seg_class = 'error'
                    html += (
                        f'        <tr>'
                        f'<td>{seg.get("No.","N/A")}</td>'
                        f'<td><a href="{seg.get("URL","#")}">{seg.get("URL","N/A")}</a></td>'
                        f'<td class="{seg_class}">{seg_status}</td>'
                        f'</tr>\n'
                    )
                html += '    </tbody></table></div>\n'
            else:
                html += '    <b>No segments available or failed to retrieve.</b>\n'
        else:
            html += '    <b>Failed to retrieve playlist data.</b>\n'

        html += '    </div>\n'

    # Closed Captions section
    cc_list = report_data.get('Closed Captions', [])
    if cc_list:
        html += '    <div class="section"><h2>Closed Captions</h2><ul>\n'
        for cc in cc_list:
            html += f'        <li><strong>Group ID:</strong> {cc.get("Group ID","N/A")} | <strong>Name:</strong> {cc.get("Name","N/A")}</li>\n'
        html += '    </ul></div>\n'

    # I-Frame Playlists section
    iframes = report_data.get('I-Frame Playlists', [])
    if iframes:
        html += (
            '    <div class="section"><h2>I-Frame Playlists</h2>\n'
            '    <table><thead><tr>'
            '<th>I-Frame URL</th><th>HTTP Status</th><th>Codecs</th>'
            '<th>Bandwidth</th><th>Resolution</th>'
            '</tr></thead><tbody>\n'
        )
        for iframe in iframes:
            iframe_status = iframe.get('HTTP Status', 'N/A')
            try:
                iframe_class = 'success' if int(iframe_status) == 200 else 'error'
            except (ValueError, TypeError):
                iframe_class = 'error'
            html += (
                f'        <tr>'
                f'<td><a href="{iframe.get("I-Frame URL","#")}">{iframe.get("I-Frame URL","N/A")}</a></td>'
                f'<td class="{iframe_class}">{iframe_status}</td>'
                f'<td>{iframe.get("Codecs","N/A")}</td>'
                f'<td>{iframe.get("Bandwidth","N/A")}</td>'
                f'<td>{iframe.get("Resolution","N/A")}</td>'
                f'</tr>\n'
            )
        html += '    </tbody></table></div>\n'

    html += '</body>\n</html>\n'

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)
        logger.info(f"HTML report written to '{output_file}'.")
    except Exception as e:
        logger.error(f'Failed to write HTML report: {e}')


# ---------------------------------------------------------------------------
# CSV report
# ---------------------------------------------------------------------------

def generate_csv_report(report_data: Dict, csv_file: str):
    """Write a flat CSV summary — one row per HLS variant stream."""
    fieldnames = [
        'Stream Type',
        'Playlist URL',
        'HTTP Status',
        'Bandwidth (bps)',
        'Video Codec',
        'Video Codec Profile',
        'Audio Codec',
        'Audio Codec Profile',
        'Resolution',
        'Frame Rate',
        'Language',
        'Channels',
        'Segment Type',
        'CMAF Compliant',
        'DRM',
        'Segments Checked',
        'Segments OK',
        'Segments Failed',
    ]

    rows = []
    for stream in report_data.get('Variant Streams', []):
        adapt_type = stream.get('Type', 'N/A')
        data = stream.get('Data') or {}
        additional_info = stream.get('Additional Info') or {}

        segments = data.get('Segments', [])
        ok_count = sum(1 for s in segments if s.get('Status') == 200)
        fail_count = len(segments) - ok_count

        drm_info = data.get('DRM Info', [])
        drm_summary = '; '.join(d.get('DRM Type', 'Unknown') for d in drm_info) if drm_info else 'None'

        rows.append({
            'Stream Type': adapt_type,
            'Playlist URL': data.get('Playlist URL', 'N/A'),
            'HTTP Status': data.get('HTTP Status', 'N/A'),
            'Bandwidth (bps)': data.get('Bandwidth', 'N/A'),
            'Video Codec': data.get('Video Codec', 'N/A'),
            'Video Codec Profile': data.get('Video Codec Translation', 'N/A'),
            'Audio Codec': data.get('Audio Codec', 'N/A'),
            'Audio Codec Profile': data.get('Audio Codec Translation', 'N/A'),
            'Resolution': additional_info.get('Resolution', 'N/A'),
            'Frame Rate': additional_info.get('Frame Rate', 'N/A'),
            'Language': additional_info.get('Language', 'N/A'),
            'Channels': additional_info.get('Channels', 'N/A'),
            'Segment Type': data.get('Segment Type', 'N/A'),
            'CMAF Compliant': data.get('CMAF Compliant', 'N/A'),
            'DRM': drm_summary,
            'Segments Checked': len(segments),
            'Segments OK': ok_count,
            'Segments Failed': fail_count,
        })

    try:
        with open(csv_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"CSV report written to '{csv_file}'.")
    except Exception as e:
        logger.error(f'Failed to write CSV report: {e}')


# ---------------------------------------------------------------------------
# CLI entry point (direct usage)
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Validate an HLS master playlist and generate HTML and CSV reports.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('HLS_URL', help='URL of the HLS master playlist (.m3u8)')
    parser.add_argument('SEGMENT_COUNT', type=int, help='Number of segments to validate per stream')
    parser.add_argument('REPORT_FILE', help='Path to the output HTML report file (.html)')
    args = parser.parse_args()

    if not args.REPORT_FILE.lower().endswith('.html'):
        logger.warning('Report file does not end with .html — appending extension.')
        args.REPORT_FILE += '.html'

    logger.info('Starting HLS analysis...')
    report_data = get_segments_and_keys_from_hls_url(args.HLS_URL, args.SEGMENT_COUNT)
    if not report_data:
        logger.error('Failed to process the HLS playlist.')
        sys.exit(1)

    generate_html_report(report_data, args.REPORT_FILE)

    csv_file = re.sub(r'\.html$', '.csv', args.REPORT_FILE, flags=re.IGNORECASE)
    generate_csv_report(report_data, csv_file)


if __name__ == '__main__':
    main()

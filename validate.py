#!/usr/bin/env python3
"""
Manifest Validator CLI

Validates DASH (.mpd) and HLS (.m3u8) streaming manifests.
Generates an HTML report and a CSV summary.

Usage:
    python validate.py <URL> [--segments N] [--output-dir DIR] [--name NAME]

Examples:
    python validate.py https://example.com/stream.mpd
    python validate.py https://example.com/stream.m3u8 --segments 5
    python validate.py https://example.com/stream.mpd --segments 3 --output-dir ./output --name my_report
"""

import argparse
import os
import sys
from datetime import datetime
from urllib.parse import urlparse


def detect_manifest_type(url: str) -> str:
    """Return 'dash', 'hls', or None based on the URL file extension."""
    path = urlparse(url).path.lower()
    if path.endswith('.mpd'):
        return 'dash'
    elif path.endswith('.m3u8'):
        return 'hls'
    return None


def main():
    parser = argparse.ArgumentParser(
        description='Validate DASH (.mpd) or HLS (.m3u8) streaming manifests.\n'
                    'Generates an HTML report and a CSV summary.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        'url',
        help='Manifest URL (.mpd for DASH, .m3u8 for HLS)'
    )
    parser.add_argument(
        '--segments', '-s',
        type=int,
        default=3,
        metavar='N',
        help='Number of segments to validate per stream (default: 3)'
    )
    parser.add_argument(
        '--output-dir', '-o',
        default='reports',
        metavar='DIR',
        help='Directory to save reports (default: reports)'
    )
    parser.add_argument(
        '--name', '-n',
        default=None,
        metavar='NAME',
        help='Base filename for reports without extension (default: auto-timestamped)'
    )

    args = parser.parse_args()

    if args.segments < 1:
        parser.error('--segments must be a positive integer.')

    manifest_type = detect_manifest_type(args.url)
    if manifest_type is None:
        print(
            'Error: Unsupported manifest format.\n'
            'Only .mpd (DASH) and .m3u8 (HLS) URLs are supported.'
        )
        sys.exit(1)

    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base_name = args.name or f'{manifest_type}_validation_{timestamp}'
    html_report = os.path.join(args.output_dir, f'{base_name}.html')
    csv_report = os.path.join(args.output_dir, f'{base_name}.csv')

    print(f'Manifest type : {manifest_type.upper()}')
    print(f'URL           : {args.url}')
    print(f'Segments      : {args.segments}')
    print(f'Output dir    : {os.path.abspath(args.output_dir)}')
    print()

    if manifest_type == 'dash':
        from validate_dash import DashValidator
        validator = DashValidator(args.url, args.segments, html_report)
        validator.run()
        validator.generate_csv_report(csv_report)

    else:  # hls
        from validate_hls import get_segments_and_keys_from_hls_url, generate_html_report, generate_csv_report
        report_data = get_segments_and_keys_from_hls_url(args.url, args.segments)
        if not report_data:
            print('Error: Failed to process the HLS playlist.')
            sys.exit(1)
        generate_html_report(report_data, html_report)
        generate_csv_report(report_data, csv_report)

    print()
    print('Reports saved:')
    print(f'  HTML : {os.path.abspath(html_report)}')
    print(f'  CSV  : {os.path.abspath(csv_report)}')


if __name__ == '__main__':
    main()

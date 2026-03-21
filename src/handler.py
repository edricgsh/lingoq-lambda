import json
import os
import re
import tempfile
import threading

import yt_dlp


def clean_vtt(vtt_content: str) -> str:
    """Parse VTT content and return clean plain text."""
    lines = vtt_content.split('\n')
    cleaned_lines = []
    prev_line = ''

    for line in lines:
        line = line.strip()

        # Skip WEBVTT header and empty lines
        if line.startswith('WEBVTT') or line == '':
            continue

        # Skip timestamp lines (e.g., 00:00:01.000 --> 00:00:04.000)
        if re.match(r'^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->', line):
            continue

        # Skip NOTE and STYLE blocks
        if line.startswith('NOTE') or line.startswith('STYLE'):
            continue

        # Skip pure numeric cue identifiers
        if re.match(r'^\d+$', line):
            continue

        # Remove HTML tags like <c>, </c>, <b>, etc.
        line = re.sub(r'<[^>]+>', '', line)

        # Remove timing tags like <00:00:01.234>
        line = re.sub(r'<\d{2}:\d{2}:\d{2}\.\d{3}>', '', line)

        # Strip extra whitespace
        line = line.strip()

        # Skip empty lines after cleaning
        if not line:
            continue

        # Deduplicate consecutive identical lines
        if line != prev_line:
            cleaned_lines.append(line)
            prev_line = line

    return ' '.join(cleaned_lines)


# Use android_vr client to bypass YouTube's PO (Proof of Origin) token requirement.
# Lambda runs on a headless server IP which YouTube flags, causing auto-generated
# captions to be withheld unless a PO token is provided. The android_vr client
# does not require a PO token and works without a JS runtime.
YT_DLP_BASE_OPTS = {
    'extractor_args': {'youtube': {'player_client': ['android_vr']}},
}


def get_video_info(youtube_url: str) -> tuple[str, str | None]:
    """Extract video title and spoken language in a single info extraction call."""
    print(f'[get_video_info] Fetching info for: {youtube_url}')
    opts = {**YT_DLP_BASE_OPTS, 'quiet': True, 'no_warnings': True, 'skip_download': True, 'noplaylist': True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
    title = info.get('title', 'Unknown Title')
    # Top-level language tag YouTube provides for the video
    lang = info.get('language')
    if not lang:
        # Fall back: find the language of the best audio format
        for fmt in (info.get('formats') or []):
            if fmt.get('acodec') not in (None, 'none') and fmt.get('language'):
                lang = fmt['language']
                break
    print(f'[get_video_info] Title: "{title}" | Duration: {info.get("duration")}s | Spoken language: {lang}')
    return title, lang


def _list_vtt_files(output_dir: str) -> list[str]:
    return [f for f in os.listdir(output_dir) if f.endswith('.vtt')]


def _fetch_subtitles_worker(youtube_url: str, output_dir: str, opts: dict, results: dict, key: str) -> None:
    """Download subtitles with given opts into output_dir, storing found files under results[key]."""
    tag = f'[worker:{key}]'
    try:
        verbose_opts = {**opts, 'quiet': False, 'no_warnings': False}
        print(f'{tag} Starting download with opts: writesubtitles={opts.get("writesubtitles")}, writeautomaticsub={opts.get("writeautomaticsub")}, langs={opts.get("subtitleslangs")}, outtmpl={opts.get("outtmpl")}')
        with yt_dlp.YoutubeDL(verbose_opts) as ydl:
            ydl.download([youtube_url])
        found = _list_vtt_files(output_dir)
        all_files = os.listdir(output_dir)
        print(f'{tag} All files in output_dir: {all_files}')
        print(f'{tag} VTT files found: {found}')
        results[key] = found
    except Exception as e:
        print(f'{tag} Exception: {type(e).__name__}: {e}')
        results[key] = []


def download_subtitles(youtube_url: str, output_dir: str) -> tuple[str, str]:
    """
    Download subtitles and return (file_path, language).
    Fetches natural and auto-generated subtitles in parallel for each language.
    Priority: native es → native en → auto es → auto en → auto-translated es.
    """
    for lang in ['es', 'en']:
        print(f'[download_subtitles] Fetching native + auto-generated in parallel for lang={lang}')

        native_dir = os.path.join(output_dir, f'native_{lang}')
        auto_dir = os.path.join(output_dir, f'auto_{lang}')
        os.makedirs(native_dir, exist_ok=True)
        os.makedirs(auto_dir, exist_ok=True)

        base_opts = {
            **YT_DLP_BASE_OPTS,
            'subtitleslangs': [lang],
            'subtitlesformat': 'vtt',
            'skip_download': True,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
        }
        native_opts = {**base_opts, 'writesubtitles': True, 'outtmpl': os.path.join(native_dir, '%(id)s.%(ext)s')}
        auto_opts = {**base_opts, 'writeautomaticsub': True, 'outtmpl': os.path.join(auto_dir, '%(id)s.%(ext)s')}

        results: dict = {}
        t_native = threading.Thread(target=_fetch_subtitles_worker, args=(youtube_url, native_dir, native_opts, results, 'native'))
        t_auto = threading.Thread(target=_fetch_subtitles_worker, args=(youtube_url, auto_dir, auto_opts, results, 'auto'))
        t_native.start()
        t_auto.start()
        t_native.join()
        t_auto.join()

        print(f'[download_subtitles] lang={lang} results: native={results.get("native")}, auto={results.get("auto")}')

        if results.get('native'):
            path = os.path.join(native_dir, results['native'][0])
            print(f'[download_subtitles] Found native subtitle: {results["native"][0]} (lang={lang})')
            return path, lang

        if results.get('auto'):
            path = os.path.join(auto_dir, results['auto'][0])
            print(f'[download_subtitles] Found auto-generated subtitle: {results["auto"][0]} (lang={lang})')
            return path, lang

        print(f'[download_subtitles] No subtitles for lang={lang}')

    # Last resort: try auto-translated Spanish from English auto-captions
    print('[download_subtitles] Trying auto-translated es from en auto-captions')
    auto_trans_dir = os.path.join(output_dir, 'auto_trans')
    os.makedirs(auto_trans_dir, exist_ok=True)
    opts = {
        **YT_DLP_BASE_OPTS,
        'writeautomaticsub': True,
        'subtitleslangs': ['es', 'en-orig'],
        'subtitlesformat': 'vtt',
        'skip_download': True,
        'noplaylist': True,
        'outtmpl': os.path.join(auto_trans_dir, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([youtube_url])

    files = _list_vtt_files(auto_trans_dir)
    if files:
        path = os.path.join(auto_trans_dir, files[0])
        print(f'[download_subtitles] Found auto-translated subtitle: {files[0]}')
        return path, 'es-auto'

    raise RuntimeError('No subtitles found for the video (tried native, auto-generated, and auto-translated for es and en)')


def handler(event, context):
    """
    Lambda handler for subtitle extraction.

    Input:
        { "youtube_url": "...", "video_id": "..." }

    Output:
        { "statusCode": 200, "title": "...", "language": "...", "subtitles": "..." }
        or
        { "statusCode": 500, "errorMessage": "..." }
    """
    print(f'[handler] Received event: {json.dumps(event)}')
    youtube_url = event.get('youtube_url')

    if not youtube_url:
        print('[handler] Missing youtube_url in event')
        return {
            'statusCode': 400,
            'errorMessage': 'youtube_url is required',
        }

    try:
        title_result = {}
        subtitle_result = {}
        subtitle_error = {}
        tmpdir_holder = {}

        def fetch_title():
            title_result['title'], title_result['spokenLanguage'] = get_video_info(youtube_url)

        def fetch_subtitles():
            tmpdir_holder['dir'] = tempfile.mkdtemp()
            try:
                path, lang = download_subtitles(youtube_url, tmpdir_holder['dir'])
                subtitle_result['path'] = path
                subtitle_result['language'] = lang
            except Exception as e:
                subtitle_error['error'] = e

        t_title = threading.Thread(target=fetch_title)
        t_subs = threading.Thread(target=fetch_subtitles)
        t_title.start()
        t_subs.start()
        t_title.join()
        t_subs.join()

        if subtitle_error:
            raise subtitle_error['error']

        title = title_result.get('title', 'Unknown Title')
        spoken_language = title_result.get('spokenLanguage')
        subtitle_path = subtitle_result['path']
        language = subtitle_result['language']
        print(f'[handler] Processing video: "{title}" spoken_language={spoken_language} subtitle_language={language}')

        with open(subtitle_path, 'r', encoding='utf-8') as f:
            vtt_content = f.read()

        print(f'[handler] Raw VTT size: {len(vtt_content)} chars')
        clean_text = clean_vtt(vtt_content)
        print(f'[handler] Cleaned subtitle size: {len(clean_text)} chars')

        if not clean_text:
            print('[handler] ERROR: Subtitles empty after cleaning')
            return {
                'statusCode': 422,
                'errorMessage': 'Subtitles were empty after cleaning',
            }

        print(f'[handler] SUCCESS — title="{title}" spoken_language="{spoken_language}" subtitle_language="{language}" subtitle_length={len(clean_text)}')
        return {
            'statusCode': 200,
            'title': title,
            'language': language,
            'spokenLanguage': spoken_language,
            'subtitles': clean_text,
            'subtitlesVtt': vtt_content,
        }

    except RuntimeError as e:
        print(f'[handler] RuntimeError: {e}')
        return {
            'statusCode': 422,
            'errorMessage': str(e),
        }
    except Exception as e:
        print(f'[handler] Unexpected error: {type(e).__name__}: {e}')
        return {
            'statusCode': 500,
            'errorMessage': f'Unexpected error: {str(e)}',
        }

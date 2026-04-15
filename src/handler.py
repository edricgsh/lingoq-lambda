import json
import os
import threading
import time

import yt_dlp


def parse_json3(json_content: str) -> str:
    """Parse YouTube json3 subtitle format and return clean plain text.

    json3 uses a rolling-window format:
    - aAppend absent/0: starts a NEW display window
    - aAppend=1: appends to the current window
    Each window maps to one subtitle screen; we collect all text and join.
    """
    import json as _json
    try:
        data = _json.loads(json_content)
    except Exception:
        return ''

    events = data.get('events') or []
    text_chunks: list[str] = []
    current_window: list[str] = []

    for event in events:
        segs = event.get('segs')
        if not segs:
            continue
        seg_texts = [s.get('utf8', '') for s in segs]

        if event.get('aAppend') == 1 and current_window:
            current_window.extend(seg_texts)
        else:
            if current_window:
                text = ''.join(current_window).strip()
                if text:
                    text_chunks.append(text)
            current_window = seg_texts

    if current_window:
        text = ''.join(current_window).strip()
        if text:
            text_chunks.append(text)

    return ' '.join(text_chunks)


# Use android_vr client to bypass YouTube's PO (Proof of Origin) token requirement.
# Lambda runs on a headless server IP which YouTube flags, causing auto-generated
# captions to be withheld unless a PO token is provided. The android_vr client
# does not require a PO token and works without a JS runtime.
YT_DLP_BASE_OPTS = {
    'extractor_args': {'youtube': {'player_client': ['android_vr']}},
}

# Webshare rotating proxy — loaded from WEBSHARE_PROXY_URL env var at cold start.
# Format: http://dezqsfeo-rotate:<pass>@p.webshare.io:80
# Webshare assigns a random IP from the pool on each request server-side.
# Set USE_PROXY=false to disable (local dev); dev/prod always use the proxy.
WEBSHARE_PROXY_URL = os.environ.get('WEBSHARE_PROXY_URL', '')
_USE_PROXY = os.environ.get('USE_PROXY', 'true').lower() != 'false' and bool(WEBSHARE_PROXY_URL)

if _USE_PROXY:
    print(f'[proxy] Using rotating proxy: {WEBSHARE_PROXY_URL.split("@")[1]}')
elif WEBSHARE_PROXY_URL:
    print('[proxy] Proxy disabled via USE_PROXY=false — requests will go direct')
else:
    print('[proxy] WARNING: WEBSHARE_PROXY_URL not set — requests will go direct')


def _proxy_opts(opts: dict) -> dict:
    if not _USE_PROXY:
        return opts
    return {**opts, 'proxy': WEBSHARE_PROXY_URL}


def _base_info_opts() -> dict:
    return _proxy_opts({
        **YT_DLP_BASE_OPTS,
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'noplaylist': True,
    })


_SIGN_IN_PHRASES = (
    'sign in to confirm',
    'sign in to your account',
    'confirm you\'re not a bot',
    'confirm you are not a bot',
    'age-restricted',
    'age restricted',
    'this video requires',
    'account is not allowed',
    'use --cookies',
    'use --cookies-from-browser',
    'confirm your age',
)

def _is_sign_in_error(exc: Exception) -> bool:
    """Return True if the exception looks like a transient Google sign-in / bot-check error."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _SIGN_IN_PHRASES)


def _extract_video_info_once(youtube_url: str) -> dict:
    """Single attempt at extract_info — callers handle retry."""
    with yt_dlp.YoutubeDL(_base_info_opts()) as ydl:
        return ydl.extract_info(youtube_url, download=False)


def extract_video_info(youtube_url: str) -> dict:
    """
    Single extract_info call that returns the full info dict.
    Raises RuntimeError immediately if no subtitles are available at all.
    Retries up to 3 times on Google sign-in / bot-check errors (with backoff).
    """
    max_attempts = 3
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            print(f'[extract_video_info] Attempt {attempt}/{max_attempts} for: {youtube_url}')
            info = _extract_video_info_once(youtube_url)
            break
        except Exception as e:
            last_exc = e
            if _is_sign_in_error(e) and attempt < max_attempts:
                wait = 2 ** attempt  # 2s, 4s
                print(f'[extract_video_info] Sign-in/bot-check error on attempt {attempt}, retrying in {wait}s: {type(e).__name__}: {e}')
                time.sleep(wait)
                continue
            raise
    else:
        # All retries exhausted
        raise last_exc  # type: ignore[misc]

    subs = info.get('subtitles') or {}
    auto_subs = info.get('automatic_captions') or {}
    print(f'[extract_video_info] Manual subtitle langs: {list(subs.keys())}')
    print(f'[extract_video_info] Auto caption langs (first 10): {list(auto_subs.keys())[:10]}')

    if not subs and not auto_subs:
        raise RuntimeError('No subtitles available for this video')

    title = info.get('title', 'Unknown Title')
    lang = info.get('language')
    if not lang:
        for fmt in (info.get('formats') or []):
            if fmt.get('acodec') not in (None, 'none') and fmt.get('language'):
                lang = fmt['language']
                break

    print(f'[extract_video_info] Title: "{title}" | Spoken language: {lang}')
    return info


def _pick_subtitle_url(formats: list[dict]) -> str | None:
    """Return the subtitle URL from a list of format dicts, preferring json3."""
    by_ext = {f['ext']: f['url'] for f in formats if f.get('url') and f.get('ext')}
    return by_ext.get('json3') or by_ext.get('vtt') or by_ext.get('srt') or next(iter(by_ext.values()), None)


def _fetch_subtitle_url(url: str, tag: str) -> str | None:
    """Download a subtitle URL via yt-dlp (respects proxy) and return its text content.
    Retries up to 3 times on sign-in/bot-check errors."""
    max_attempts = 3
    opts = _proxy_opts({
        **YT_DLP_BASE_OPTS,
        'quiet': True,
        'no_warnings': True,
    })

    for attempt in range(1, max_attempts + 1):
        print(f'[{tag}] Fetching subtitle URL (attempt {attempt}/{max_attempts})')
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                response = ydl.urlopen(url)
                content = response.read().decode('utf-8')
            print(f'[{tag}] Downloaded {len(content)} bytes')
            return content
        except Exception as e:
            if _is_sign_in_error(e) and attempt < max_attempts:
                wait = 2 ** attempt  # 2s, 4s
                print(f'[{tag}] Sign-in/bot-check error on attempt {attempt}, retrying in {wait}s: {type(e).__name__}: {e}')
                time.sleep(wait)
                continue
            print(f'[{tag}] Failed to fetch subtitle URL: {type(e).__name__}: {e}')
            return None


def download_subtitles(info: dict, target_lang: str = 'en') -> tuple[str, str]:
    """
    Select and download the best available subtitle from the pre-fetched info dict.
    Returns (vtt_content, language).

    Priority: native target_lang → native en → auto target_lang → auto en → auto-translated target_lang.
    Uses direct URL fetch instead of ydl.download() to minimise overhead.
    """
    subs = info.get('subtitles') or {}
    auto_subs = info.get('automatic_captions') or {}

    langs_to_try = [target_lang] if target_lang == 'en' else [target_lang, 'en']

    for lang in langs_to_try:
        # Try native first, then auto-generated, in parallel
        native_formats = subs.get(lang, [])
        auto_formats = auto_subs.get(lang, [])

        native_url = _pick_subtitle_url(native_formats) if native_formats else None
        auto_url = _pick_subtitle_url(auto_formats) if auto_formats else None

        if not native_url and not auto_url:
            print(f'[download_subtitles] No subtitle URLs for lang={lang}')
            continue

        print(f'[download_subtitles] Fetching native + auto-generated in parallel for lang={lang}')
        results: dict = {}

        def fetch_native(url=native_url):
            if url:
                results['native'] = _fetch_subtitle_url(url, f'native:{lang}')

        def fetch_auto(url=auto_url):
            if url:
                results['auto'] = _fetch_subtitle_url(url, f'auto:{lang}')

        threads = []
        if native_url:
            threads.append(threading.Thread(target=fetch_native))
        if auto_url:
            threads.append(threading.Thread(target=fetch_auto))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        print(f'[download_subtitles] lang={lang} results: native={bool(results.get("native"))}, auto={bool(results.get("auto"))}')

        if results.get('native'):
            return results['native'], lang
        if results.get('auto'):
            return results['auto'], lang

        print(f'[download_subtitles] No subtitles fetched for lang={lang}')

    # Last resort: auto-translated target_lang from English auto-captions
    if target_lang != 'en':
        print(f'[download_subtitles] Trying auto-translated {target_lang} from en auto-captions')
        trans_formats = auto_subs.get(target_lang, [])
        trans_url = _pick_subtitle_url(trans_formats) if trans_formats else None
        if trans_url:
            content = _fetch_subtitle_url(trans_url, f'auto-trans:{target_lang}')
            if content:
                return content, f'{target_lang}-auto'

    tried = [target_lang] if target_lang == 'en' else [target_lang, 'en']
    raise RuntimeError(f'No subtitles found for the video (tried native, auto-generated, and auto-translated for {" and ".join(tried)})')


def handler(event, context):
    """
    Lambda handler for subtitle extraction.

    Input:
        { "youtube_url": "...", "video_id": "..." }

    Output:
        { "statusCode": 200, "title": "...", "language": "...", "subtitles": "..." }
        or
        { "statusCode": 422, "errorMessage": "..." }
        or
        { "statusCode": 500, "errorMessage": "..." }
    """
    print(f'[handler] Received event: {json.dumps(event)}')
    youtube_url = event.get('youtube_url')
    target_language = event.get('language') or 'en'

    if not youtube_url:
        print('[handler] Missing youtube_url in event')
        return {
            'statusCode': 400,
            'errorMessage': 'youtube_url is required',
        }

    try:
        # Single extract_info call: gets title, language, and subtitle availability.
        # Raises RuntimeError immediately if no subtitles exist — fast fail.
        info = extract_video_info(youtube_url)

        title = info.get('title', 'Unknown Title')
        spoken_language = info.get('language')
        if not spoken_language:
            for fmt in (info.get('formats') or []):
                if fmt.get('acodec') not in (None, 'none') and fmt.get('language'):
                    spoken_language = fmt['language']
                    break

        print(f'[handler] Processing video: "{title}" spoken_language={spoken_language}')

        # Download only the selected subtitle file directly via URL fetch.
        raw_content, language = download_subtitles(info, target_language)

        print(f'[handler] Raw subtitle size: {len(raw_content)} chars')
        # Detect format: json3 starts with '{', VTT starts with 'WEBVTT'
        is_json3 = raw_content.lstrip().startswith('{')
        if is_json3:
            clean_text = parse_json3(raw_content)
        else:
            # Fallback: strip VTT markup with a simple approach
            import re as _re
            lines = []
            prev = ''
            for line in raw_content.split('\n'):
                line = line.strip()
                if not line or line.startswith('WEBVTT') or line.startswith('NOTE') or line.startswith('STYLE'):
                    continue
                if _re.match(r'^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->', line) or _re.match(r'^\d+$', line):
                    continue
                line = _re.sub(r'<[^>]+>', '', line).strip()
                if line and line != prev:
                    lines.append(line)
                    prev = line
            clean_text = ' '.join(lines)

        print(f'[handler] Cleaned subtitle size: {len(clean_text)} chars')

        if not clean_text:
            print('[handler] ERROR: Subtitles empty after cleaning')
            return {
                'statusCode': 422,
                'errorMessage': 'Subtitles were empty after cleaning',
            }

        print(f'[handler] SUCCESS — title="{title}" spoken_language="{spoken_language}" subtitle_language="{language}" subtitle_length={len(clean_text)}')
        result = {
            'statusCode': 200,
            'title': title,
            'language': language,
            'spokenLanguage': spoken_language,
            'subtitles': clean_text,
        }
        if is_json3:
            result['subtitleJson'] = json.loads(raw_content)
        return result

    except RuntimeError as e:
        print(f'[handler] RuntimeError: {e}')
        return {
            'statusCode': 422,
            'errorMessage': str(e),
        }
    except Exception as e:
        print(f'[handler] Unexpected error: {type(e).__name__}: {e}')
        if _is_sign_in_error(e):
            return {
                'statusCode': 422,
                'errorMessage': 'This video is not available for processing. It may be age-restricted or region-locked.',
            }
        return {
            'statusCode': 500,
            'errorMessage': 'An unexpected error occurred while processing the video.',
        }

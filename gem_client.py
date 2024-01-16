import urllib.parse
import ssl
import re
import socket
from dataclasses import dataclass
from typing import Optional, Tuple


DEFAULT_PORT = 1965
DEFAULT_MAX_BYTES = 32 * 1024 * 1024 # 32 MB

class GeminiError(Exception):
    def __init__(self, message: str, retry_in_browser: bool=False):
        self.message = message
        self.retry_in_browser = retry_in_browser

@dataclass
class GeminiResponse:
    status: int
    meta: str
    body: Optional[bytes]

    def broad_status(self) -> int:
        return self.status // 10

    def decoded_status(self) -> str:
        specific_statuses = {
            10: 'INPUT',
            11: 'SENSITIVE INPUT',
            20: 'SUCCESS',
            30: 'REDIRECT - TEMPORARY',
            31: 'REDIRECT - PERMANENT',
            40: 'TEMPORARY FAILURE',
            41: 'SERVER UNAVAILABLE',
            42: 'CGI ERROR',
            43: 'PROXY ERROR',
            44: 'SLOW DOWN',
            50: 'PERMANENT FAILURE',
            51: 'NOT FOUND',
            52: 'GONE',
            53: 'PROXY REQUEST REFUSED',
            59: 'BAD REQUEST',
            60: 'CLIENT CERTIFICATE REQUIRED',
            61: 'CERTIFICATE NOT AUTHORISED',
            62: 'CERTIFICATE NOT VALID',
        }

        if self.status in specific_statuses:
            return specific_statuses[self.status]
        if self.broad_status() * 10 in specific_statuses:
            return specific_statuses[self.broad_status() * 10]
        return 'UNKNOWN STATUS???'

    def get_mime_type_and_charset_from_meta(self) -> Tuple[str, str]:
        mime_type, *mime_params = map(str.strip, self.meta.split(';'))

        mime_type = mime_type or 'text/gemini'
        charset = None

        for param in mime_params:
            if m := re.match('^charset=(.*)$', param):
                charset = m.group(1)

        charset = charset or 'utf-8'

        return mime_type, charset

    def decoded_body_or_err(self) -> Tuple[str, str]:
        if self.body is None:
            raise GeminiError('no body!')

        mime_type, charset = self.get_mime_type_and_charset_from_meta()

        canonicalised = charset.lower().replace('-', '').replace('_', '')

        mapping = {
            'utf8': 'utf-8',
            'utf16le': 'utf-16-le',
            'utf16be': 'utf-16-be',
            'utf32le': 'utf-32-le',
            'utf32be': 'utf-32-be',
            'windows1252': 'cp1252',
            'cp437': 'cp437',
            'ebcdicatde': 'cp500', # i think...
            'utf16': 'utf_16',
            'utf32': 'utf_32',
            'iso88591': 'latin-1',
            'usascii': 'ascii',
        }

        try:
            encoding = mapping[canonicalised]
        except KeyError:
            raise GeminiError(f'Unknown charset {charset}')

        try:
            return mime_type, self.body.decode(encoding)
        except UnicodeDecodeError:
            raise GeminiError(f'Failed to decode body with {encoding}')

class TrustPolicy:
    pass

def fetch_gem_raw(url: str, trust_policy: TrustPolicy, max_num_bytes: int=DEFAULT_MAX_BYTES) -> bytes:
    parsed_url = urllib.parse.urlparse(url)

    host = parsed_url.hostname
    port = parsed_url.port or DEFAULT_PORT

    if not host:
        raise GeminiError('Bad url', retry_in_browser=True)

    if parsed_url.scheme != 'gemini':
        raise GeminiError('Unsupported protcol ' + parsed_url.scheme, retry_in_browser=True)

    if len(url) > 1024:
        raise GeminiError('Request url too long')

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    TIMEOUT = 10
    try:
        tcp_socket = socket.create_connection((host, port), timeout=TIMEOUT)

        ssl_socket = context.wrap_socket(
            tcp_socket, server_hostname=host,
        )

        # print(ssl_socket.getpeercert(binary_form=True))

        ssl_socket.sendall(url.encode('utf-8') + b'\r\n')

        all_data = []
        num_response_bytes = 0
        while True:
            data = ssl_socket.recv(2048)
            all_data.append(data)

            num_response_bytes += len(data)
            if num_response_bytes > max_num_bytes:
                raise GeminiError('Too many bytes')

            if not data: break

        ssl_socket.close()
    except socket.timeout:
        raise GeminiError('Timeout')
    except socket.gaierror:
        raise GeminiError('Error getting address')
    except socket.herror:
        raise GeminiError('Host error')
    except OSError:
        raise GeminiError('General OS error while fetching')
    except KeyboardInterrupt:
        raise GeminiError('Request interrupted')


    full_response = b''.join(all_data)

    return full_response

def fetch_gem(url: str, trust_policy: TrustPolicy, max_num_bytes: int=DEFAULT_MAX_BYTES) -> GeminiResponse:
    full_response = fetch_gem_raw(url, trust_policy, max_num_bytes)

    header, *body = full_response.split(b'\r\n', 1)
    header_match = re.match(r'^(\d\d) ?(.{0,1024})$', header.decode('utf-8'))

    if not header_match:
        raise GeminiError('Malformed status line')

    status = int(header_match.group(1))
    meta = header_match.group(2)

    if meta == '':
        meta = 'text/gemini; charset=utf-8'

    response = GeminiResponse(
        status, meta, body[0] if body and body[0] else None
    )

    if response.body is not None and response.broad_status() != 2:
        raise GeminiError(f'Got body when didn\'t expect one ({status=})')
    if response is None and response.broad_status() == 2:
        raise GeminiError(f'Didn\'t get body when one was expected ({status=})')

    return response

if __name__ == '__main__':
    print(fetch_gem('gemini://gemini.circumlunar.space/docs/', TrustPolicy()))
    print(fetch_gem('gemini://geminispace.info/search', TrustPolicy()))
    print(fetch_gem('gemini://gemini.thebackupbox.net/test/torture/', TrustPolicy()))

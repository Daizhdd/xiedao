"""TLS roots for macOS apps that run without a separately installed Python."""
from functools import lru_cache
import ssl
import sys


@lru_cache(maxsize=1)
def https_context():
    context = ssl.create_default_context()
    if sys.platform == 'darwin':
        import certifi
        # Add portable public roots; retain system / SSL_CERT_FILE roots as well.
        context.load_verify_locations(cafile=certifi.where())
    return context


def request_options(request):
    if sys.platform == 'darwin' and request.type == 'https':
        return {'context': https_context()}
    return {}

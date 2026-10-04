"""macOS bundles retain strict TLS validation and leave local HTTP usable."""
import ssl
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

from ai import transport


class MacTransportTests(unittest.TestCase):
    def tearDown(self):
        transport.https_context.cache_clear()

    def test_mac_adds_packaged_roots_and_preserves_strict_tls(self):
        context = ssl.create_default_context()
        certifi = SimpleNamespace(where=lambda: '/bundle/certifi/cacert.pem')
        with patch.object(transport.sys, 'platform', 'darwin'), \
                patch.dict('sys.modules', {'certifi': certifi}), \
                patch.object(transport.ssl, 'create_default_context', return_value=context), \
                patch.object(context, 'load_verify_locations') as load:
            self.assertIs(transport.https_context(), context)
            self.assertEqual(load.call_args.kwargs, {'cafile': '/bundle/certifi/cacert.pem'})
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            request = urllib.request.Request('https://example.invalid')
            self.assertIs(transport.request_options(request)['context'], context)

    def test_local_http_and_windows_keep_existing_transport(self):
        with patch.object(transport.sys, 'platform', 'darwin'):
            self.assertEqual(transport.request_options(
                urllib.request.Request('http://localhost:11434/v1')), {})
        with patch.object(transport.sys, 'platform', 'win32'):
            self.assertEqual(transport.request_options(
                urllib.request.Request('https://example.invalid')), {})


if __name__ == '__main__':
    unittest.main()

import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_console
from test_console import call


class HtmlPreviewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_console.ConsoleTests.asyncSetUp
    asyncTearDown = test_console.ConsoleTests.asyncTearDown

    async def test_preview_uses_native_file_access_and_an_opaque_document(self):
        body = '<!doctype html><style>body{color:red}</style><button>交互</button><script>window.ready=true</script>'
        native = AsyncMock(return_value={'dataBase64': base64.b64encode(body.encode()).decode()})
        self.host.native = SimpleNamespace(call=native)
        sent = await call(self.app, 'GET', '/api/workspace-preview?path=%2Ffixture%2Freport.HTML&revision=1')
        self.assertEqual(sent[0]['status'], 200)
        self.assertEqual(sent[1]['body'], body.encode())
        self.assertEqual(native.call_args.args[:2], ('fs/readFile', {'path': '/fixture/report.HTML'}))
        headers = dict(sent[0]['headers'])
        policy = headers[b'content-security-policy'].decode()
        self.assertIn('sandbox allow-scripts;', policy)
        self.assertNotIn('allow-same-origin', policy)
        self.assertIn("script-src 'unsafe-inline'", policy)
        self.assertIn("connect-src 'none'", policy)
        self.assertIn("form-action 'none'", policy)
        self.assertEqual(headers[b'cache-control'], b'no-store')
        self.assertEqual(headers[b'referrer-policy'], b'no-referrer')

    async def test_invalid_paths_do_not_read_files(self):
        native = AsyncMock()
        self.host.native = SimpleNamespace(call=native)
        for query in ['', 'path=report.html', 'path=%2Ffixture%2Fprivate.json',
                      'path=https%3A%2F%2Fexample.test%2Fa.html', 'path=%2F%2Fhost%2Fa.html',
                      'path=%2Fa.html&path=%2Fb.html', 'path=%2Fa%00.html']:
            with self.subTest(query=query):
                sent = await call(self.app, 'GET', '/api/workspace-preview?' + query)
                self.assertEqual(sent[0]['status'], 409)
        native.assert_not_called()

    async def test_invalid_content_and_native_denial_remain_errors(self):
        native = AsyncMock(return_value={'dataBase64': 'not base64!'})
        self.host.native = SimpleNamespace(call=native)
        self.assertEqual((await call(self.app, 'GET', '/api/workspace-preview?path=/a.html'))[0]['status'], 409)
        native.side_effect = ValueError('native file access denied')
        self.assertEqual((await call(self.app, 'GET', '/api/workspace-preview?path=/a.html'))[0]['status'], 409)

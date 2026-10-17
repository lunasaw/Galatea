import unittest

from codex_agent.console.mcp_apps import resource_document


class McpAppDocumentTests(unittest.TestCase):
    def test_optional_metadata_is_validated_before_building_csp(self):
        for metadata in (None, [], {'ui': None}, {'ui': {'csp': None}},
                         {'ui': {'csp': {'resourceDomains': 'https://cdn.example.test'}}}):
            _, policy = resource_document({'contents': [{
                'uri': 'ui://fixture/app', 'mimeType': 'text/html', 'text': 'hello', '_meta': metadata,
            }]}, 'ui://fixture/app', 'localhost')
            self.assertIn("connect-src 'none'", policy)
            self.assertNotIn('cdn.example.test', policy)

    def test_html_has_an_opaque_origin_and_only_declared_external_origins(self):
        html = '<script>parent.postMessage({hello: true}, "*")</script>'
        body, policy = resource_document({'contents': [{
            'uri': 'ui://fixture/app', 'mimeType': 'text/html;profile=mcp-app', 'text': html,
            '_meta': {'ui': {'csp': {
                'resourceDomains': ['https://cdn.example.test', "https://bad; script-src *", 'http://localhost:18880'],
                'connectDomains': ['https://api.example.test', 'http://localhost:18880'],
            }}},
        }]}, 'ui://fixture/app', 'localhost:18880')
        self.assertEqual(body, html.encode())
        self.assertIn('sandbox allow-scripts;', policy)
        self.assertNotIn('allow-same-origin', policy)
        self.assertNotIn('localhost', policy)
        self.assertNotIn('https://bad', policy)
        self.assertIn('connect-src https://api.example.test;', policy)
        self.assertIn("frame-src 'none'", policy)

    def test_other_resources_cannot_be_rendered_as_the_requested_app(self):
        with self.assertRaises(ValueError):
            resource_document({'contents': [{'uri': 'ui://other', 'mimeType': 'text/html', 'text': 'wrong'}]}, 'ui://fixture/app', 'localhost')
        with self.assertRaises(ValueError):
            resource_document({'contents': [{'uri': 'ui://fixture/app', 'mimeType': 'text/plain', 'text': 'wrong'}]}, 'ui://fixture/app', 'localhost')

    def test_base_uri_and_host_aliases_cannot_weaken_network_isolation(self):
        _, policy = resource_document({'contents': [None, {'uri': 'ui://fixture/app', 'mimeType': None}, {
            'uri': 'ui://fixture/app', 'mimeType': 'text/html', 'text': 'hello',
            '_meta': {'ui': {'csp': {
                'baseUriDomains': ['https://cdn.example.test'],
                'connectDomains': ['https://console.example.test:443', 'https://*.example.test',
                                   'https://api.example.test:99999', 'https://api.example.test'],
            }}},
        }]}, 'ui://fixture/app', 'console.example.test')
        self.assertIn('base-uri https://cdn.example.test;', policy)
        self.assertIn('connect-src https://api.example.test;', policy)
        self.assertNotIn('console.example.test', policy)
        self.assertNotIn('*.example.test', policy)
        self.assertNotIn('99999', policy)

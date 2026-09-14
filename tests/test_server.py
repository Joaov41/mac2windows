import concurrent.futures
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('two_way_server', ROOT / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
s.app.template_folder = str(ROOT / 'templates')

class Transfers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        s.UPLOAD_DIR = str(self.root / 'transfers')
        s.RECEIVE_DIR = str(self.root / 'received')
        Path(s.UPLOAD_DIR).mkdir()
        Path(s.RECEIVE_DIR).mkdir()
        s._sent_files.clear()
        s._clipboard_content.update(text='', timestamp=0)
        s.app.config['TESTING'] = True
        self.client = s.app.test_client()
        self.headers = {'X-Mac2Windows-Token': s._incoming_token, 'Origin': 'http://localhost'}

    def tearDown(self):
        self.temp.cleanup()

    def upload(self, content=b'file bytes', name='report.txt', client=None):
        return (client or self.client).post('/api/receive/file', data={'file': (io.BytesIO(content), name)}, headers=self.headers)

    def test_render_and_old_reads(self):
        # Resolve templates relative to the repository root.
        s.app.template_folder = str(ROOT / 'templates')
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(s._incoming_token.encode(), response.data)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn(b'Send files to Mac', response.data)
        s.set_clipboard('Original Mac clipboard')
        self.assertEqual(self.client.get('/api/clipboard').json, {'text': 'Original Mac clipboard'})
        outgoing = Path(s.UPLOAD_DIR) / 'outgoing.txt'
        outgoing.write_bytes(b'original outgoing file')
        s.add_file(str(outgoing))
        self.assertEqual(self.upload().status_code, 201)
        state = self.client.get('/api/state').json
        self.assertEqual(state['clipboard'], 'Original Mac clipboard')
        self.assertEqual([f['name'] for f in state['files']], ['outgoing.txt'])
        downloaded = self.client.get('/api/file/outgoing.txt')
        self.assertEqual(downloaded.data, b'original outgoing file')
        downloaded.close()
        self.assertEqual(self.client.get('/api/file/missing').status_code, 404)
        self.assertEqual(self.client.post('/api/clear').status_code, 200)
        self.assertFalse(outgoing.exists())
        self.assertEqual((Path(s.RECEIVE_DIR) / 'report.txt').read_bytes(), b'file bytes')
        self.assertEqual(self.client.get('/api/state').json['files'], [])
        self.assertEqual(self.client.get('/api/clipboard').json['text'], '')

    def test_clipboard_exact_utf8_and_no_outbound_change(self):
        s.set_clipboard('existing outgoing')
        text = 'Windows → Mac\nPortuguês: ação €25\n  trailing spaces  '
        with patch.object(s.subprocess, 'run') as run:
            response = self.client.post('/api/receive/clipboard', json={'text': text}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['ok'])
        args, kwargs = run.call_args
        self.assertEqual(args, (['/usr/bin/pbcopy'],))
        self.assertEqual(kwargs['input'].decode('utf-8'), text)
        self.assertTrue(kwargs['check'])
        self.assertEqual(self.client.get('/api/clipboard').json['text'], 'existing outgoing')

    def test_clipboard_validation_and_failures(self):
        with patch.object(s.subprocess, 'run') as run:
            for body in ({}, [], {'text': ''}, {'text': 123}, {'text': '\ud800'}):
                self.assertEqual(self.client.post('/api/receive/clipboard', json=body, headers=self.headers).status_code, 400)
            with patch.object(s, 'MAX_CLIPBOARD_BYTES', 5):
                self.assertEqual(self.client.post('/api/receive/clipboard', json={'text': 'ééé'}, headers=self.headers).status_code, 413)
            run.assert_not_called()
            for failure in (OSError('unavailable'), subprocess.TimeoutExpired('pbcopy', 5), subprocess.CalledProcessError(1, 'pbcopy')):
                run.side_effect = failure
                response = self.client.post('/api/receive/clipboard', json={'text': 'test'}, headers=self.headers)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('ok', response.json)

    def test_write_request_guards(self):
        with patch.object(s.subprocess, 'run') as run:
            for headers in ({}, {'X-Mac2Windows-Token': 'stale'}, {**self.headers, 'Origin': 'http://outside.example'}):
                for endpoint in ('clipboard', 'file'):
                    response = self.client.post('/api/receive/' + endpoint, json={'text': 'test'}, headers=headers)
                    self.assertEqual(response.status_code, 403)
            run.assert_not_called()
        self.assertEqual(list(Path(s.RECEIVE_DIR).iterdir()), [])

    def test_upload_bytes_safe_names_and_duplicates(self):
        data = bytes(range(256)) * 20
        for name, expected in (('../../report.txt', 'report.txt'), ('C:\\Users\\Windows\\report.txt', 'report (2).txt'), ('report.txt', 'report (3).txt'), ('ação.txt', 'acao.txt')):
            response = self.upload(data, name)
            self.assertEqual(response.status_code, 201, response.json)
            self.assertEqual(response.json['name'], expected)
            self.assertEqual(response.json['size'], len(data))
            self.assertEqual((Path(s.RECEIVE_DIR) / expected).read_bytes(), data)
        self.assertEqual(self.upload(b'', 'empty.txt').status_code, 201)
        self.assertEqual(self.upload(name='..').status_code, 400)
        self.assertEqual(self.client.post('/api/receive/file', headers=self.headers).status_code, 400)
        self.assertFalse(any(p.name.startswith('.incoming-') for p in Path(s.RECEIVE_DIR).iterdir()))

    def test_concurrent_duplicate_names(self):
        def upload(i):
            with s.app.test_client() as client:
                response = self.upload(str(i).encode(), 'same.txt', client)
                self.assertEqual(response.status_code, 201)
                return response.json['name'], str(i).encode()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(upload, range(6)))
        self.assertEqual(len({name for name, _ in results}), 6)
        for name, expected in results:
            self.assertEqual((Path(s.RECEIVE_DIR) / name).read_bytes(), expected)

    def test_upload_limit_failure_cleanup_and_retry(self):
        with patch.object(s, 'MAX_FILE_BYTES', 8):
            self.assertEqual(self.upload(b'012345678').status_code, 413)
            self.assertEqual(self.upload(b'01234567').status_code, 201)
        before = set(Path(s.RECEIVE_DIR).iterdir())
        with patch.object(s.os, 'link', side_effect=OSError('disk error')):
            with self.assertLogs(s.app.logger, level='ERROR'):
                self.assertEqual(self.upload(b'another file').status_code, 507)
        self.assertEqual(set(Path(s.RECEIVE_DIR).iterdir()), before)
        self.assertEqual(self.upload(b'retry').status_code, 201)

    def test_browser_direction(self):
        agents = {
            'Mac': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15',
            'Windows': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36',
        }
        for platform, agent in agents.items():
            html = self.client.get('/', headers={'User-Agent': agent}).data.decode()
            target = 'Windows' if platform == 'Mac' else 'Mac'
            self.assertIn(f'id="to-mac-title">Send to {target}</h2>', html)
            self.assertIn(f'>Send files to {target}</button>', html)
            self.assertIn('const clientIsMac = ' + ('true' if platform == 'Mac' else 'false'), html)
            self.assertNotIn('A shared space', html)
        mac_html = self.client.get('/', headers={'User-Agent': agents['Mac']}).data.decode()
        self.assertIn('Available on Windows', mac_html)
        self.assertNotIn('Then paste on your Mac', mac_html)

    def test_mac_text_is_published_for_windows_without_writing_mac_clipboard(self):
        text = 'Mac → Windows\nPortuguês: ação €25  '
        with patch.object(s.subprocess, 'run') as run:
            response = self.client.post('/api/share/clipboard', json={'text': text}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            run.assert_not_called()
        self.assertEqual(self.client.get('/api/state').json['clipboard'], text)
        self.assertEqual(self.client.get('/api/clipboard').json['text'], text)
        self.assertGreater(self.client.get('/api/state').json['clipboard_time'], 0)

    def test_mac_files_are_downloadable_on_windows_and_do_not_overwrite(self):
        for content, expected in ((b'first', 'report.txt'), (b'second', 'report (2).txt')):
            response = self.client.post('/api/share/file', data={'file': (io.BytesIO(content), '../../report.txt')}, headers=self.headers)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json['name'], expected)
            download = self.client.get('/api/file/' + expected)
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.data, content)
            download.close()
        self.assertEqual([f['name'] for f in self.client.get('/api/state').json['files']], ['report.txt', 'report (2).txt'])
        self.assertEqual(list(Path(s.RECEIVE_DIR).iterdir()), [])
        self.assertFalse(any(p.name.startswith('.incoming-') for p in Path(s.UPLOAD_DIR).iterdir()))

    def test_outgoing_write_guards_and_limits(self):
        for headers in ({}, {'X-Mac2Windows-Token': 'stale'}, {**self.headers, 'Origin': 'http://outside.example'}):
            for endpoint in ('clipboard', 'file'):
                self.assertEqual(self.client.post('/api/share/' + endpoint, json={'text': 'test'}, headers=headers).status_code, 403)
        with patch.object(s, 'MAX_CLIPBOARD_BYTES', 5):
            self.assertEqual(self.client.post('/api/share/clipboard', json={'text': 'ééé'}, headers=self.headers).status_code, 413)
        self.assertEqual(self.client.post('/api/share/clipboard', json={'text': ''}, headers=self.headers).status_code, 400)
        with patch.object(s, 'MAX_FILE_BYTES', 5):
            response = self.client.post('/api/share/file', data={'file': (io.BytesIO(b'123456'), 'too-big.txt')}, headers=self.headers)
            self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.get('/api/state').json['files'], [])
        self.assertEqual(list(Path(s.UPLOAD_DIR).iterdir()), [])

if __name__ == '__main__':
    unittest.main(verbosity=2)

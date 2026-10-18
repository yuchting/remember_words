"""云端朗读检查；临时数据库与 MockTransport，不访问真实服务。"""
import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import db, main, speech

MP3 = b'ID3test-audio'


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB_PATH
        db.DB_PATH = Path(self.temp.name) / 'words.sqlite3'
        db.initialize()
        self.account = {'id': 1, 'role': 'parent'}
        main.app.dependency_overrides[main.user] = lambda: self.account
        self.client = TestClient(main.app)
        main.speech_requests.clear()
        with db.connect() as conn:
            conn.execute("INSERT INTO users VALUES(1,'parent','hash','parent',1)")
            conn.execute("INSERT INTO decks VALUES(1,1,'deck',0,NULL)")
            conn.execute("INSERT INTO words(id,deck_id,word,meaning,example) VALUES(1,1,'apple','苹果','An apple.')")
            conn.execute("INSERT INTO study_sessions VALUES(1,1,1,'new','active',1)")
            conn.execute('INSERT INTO study_items(id,session_id,word_id,round_number,position) VALUES(1,1,1,1,0)')

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()
        db.DB_PATH = self.previous
        self.temp.cleanup()

    def configured(self):
        return speech.Settings(primary='azure', fallback='google',
                               azure=speech.Provider(key='azure-secret'),
                               google=speech.Provider(key='google-secret', voice='en-US-Neural2-F'))

    def test_settings_secrets_validation_and_permissions(self):
        config = self.configured()
        response = self.client.put('/api/admin/speech', json=config.model_dump())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('secret', response.text)
        self.assertTrue(response.json()['azure']['key_set'])
        self.assertEqual((speech.directory() / 'settings.json').stat().st_mode & 0o777, 0o600)
        config.azure.key = ''
        speech.save(config)
        self.assertEqual(speech.load().azure.key, 'azure-secret')
        config.azure.region = 'evil.example/path'
        self.assertEqual(self.client.put('/api/admin/speech', json=config.model_dump()).status_code, 422)
        config = self.configured()
        config.azure.clear_key = True
        with self.assertRaises(HTTPException):
            speech.save(config)
        config.primary = 'off'
        speech.save(config)
        self.assertFalse(speech.load().azure.key)
        self.account['role'] = 'child'
        for method, path in [('get', '/api/admin/speech'), ('delete', '/api/admin/speech/cache'), ('get', '/api/admin/speech/cache'), ('post', '/api/admin/speech/test')]:
            self.assertEqual(getattr(self.client, method)(path).status_code, 403)

    def test_reveal_ownership_and_validation(self):
        self.assertEqual(self.client.post('/api/speech', json={'word_id': 1, 'kind': 'word'}).status_code, 403)
        self.assertEqual(self.client.post('/api/speech', json={'word_id': 2, 'kind': 'word'}).status_code, 404)
        self.assertEqual(self.client.post('/api/speech', json={'word_id': 1, 'kind': 'arbitrary'}).status_code, 422)
        with db.connect() as conn:
            conn.execute('UPDATE study_items SET hint_used=1')
        with patch.object(speech, 'audio', AsyncMock(return_value=MP3)) as mock:
            response = self.client.post('/api/speech', json={'word_id': 1, 'kind': 'example'})
            self.assertEqual(response.content, MP3)
            mock.assert_awaited_once_with('An apple.')
        self.account['id'] = 2
        self.assertEqual(self.client.post('/api/speech', json={'word_id': 1, 'kind': 'word'}).status_code, 404)
        self.account['id'] = 1
        with db.connect() as conn:
            conn.execute("UPDATE words SET example=''")
        self.assertEqual(self.client.post('/api/speech', json={'word_id': 1, 'kind': 'example'}).status_code, 422)
        for _ in range(30):
            response = self.client.post('/api/speech', json={'word_id': 1, 'kind': 'word'})
        self.assertEqual(response.status_code, 429)

    def test_fallback_cache_eviction_and_clear(self):
        speech.save(self.configured())
        calls = []

        async def fake(client, name, provider, text):
            calls.append(name)
            if name == 'azure':
                raise httpx.ConnectError('offline')
            return MP3

        with patch.object(speech, 'synthesize', fake), patch.object(speech, 'MAX_CACHE', len(MP3) * 2):
            self.assertEqual(asyncio.run(speech.audio('apple')), MP3)
            self.assertEqual(calls, ['azure', 'google'])
            asyncio.run(speech.audio('apple'))
            self.assertEqual(len(calls), 2)
            asyncio.run(speech.audio('banana'))
            asyncio.run(speech.audio('orange'))
            stats = self.client.get('/api/admin/speech/cache').json()
            self.assertEqual(stats['files'], 2)
            self.assertEqual(stats['bytes'], len(MP3) * 2)
            self.assertEqual(self.client.delete('/api/admin/speech/cache').json()['bytes'], 0)
        with patch.object(speech, 'synthesize', AsyncMock(return_value=b'not audio')):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(speech.audio('bad'))
            self.assertEqual(caught.exception.status_code, 502)
        speech.save(speech.Settings())
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(speech.audio('off'))
        self.assertEqual(caught.exception.status_code, 409)

    def test_individual_provider_uses_draft_without_save_cache_or_fallback(self):
        speech.save(self.configured())
        payload = {'provider': 'google', 'config': {'key': 'draft-key', 'voice': 'en-GB-Neural2-A'}}
        with patch.object(speech, 'synthesize', AsyncMock(return_value=MP3)) as mock:
            result = self.client.post('/api/admin/speech/test-provider', json=payload)
            self.assertEqual(result.content, MP3)
            self.assertEqual(mock.await_args.args[1], 'google')
            self.assertEqual(mock.await_args.args[2].key, 'draft-key')
            self.assertEqual(mock.await_args.args[2].voice, 'en-GB-Neural2-A')
            payload['config']['key'] = ''
            self.client.post('/api/admin/speech/test-provider', json=payload)
            self.assertEqual(mock.await_args.args[2].key, 'google-secret')
        self.assertEqual(speech.load().google.key, 'google-secret')
        self.assertEqual(speech.cache_stats()['files'], 0)
        with patch.object(speech, 'synthesize', AsyncMock(side_effect=httpx.ConnectError('offline'))) as mock:
            self.assertEqual(self.client.post('/api/admin/speech/test-provider', json=payload).status_code, 502)
            self.assertEqual(mock.await_count, 1)
        payload['config']['clear_key'] = True
        self.assertEqual(self.client.post('/api/admin/speech/test-provider', json=payload).status_code, 422)
        payload['provider'] = 'arbitrary'
        self.assertEqual(self.client.post('/api/admin/speech/test-provider', json=payload).status_code, 422)
        self.account['role'] = 'child'
        self.assertEqual(self.client.post('/api/admin/speech/test-provider', json=payload).status_code, 403)

    def test_cache_disk_failure_returns_actionable_error(self):
        speech.save(self.configured())
        with patch.object(speech, 'synthesize', AsyncMock(return_value=MP3)), patch.object(Path, 'write_bytes', side_effect=OSError('disk full')):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(speech.audio('apple'))
            self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(speech.cache_stats()['files'], 0)

    def test_primary_cache_key_and_failed_requests_not_cached(self):
        speech.save(self.configured())
        with patch.object(speech, 'synthesize', AsyncMock(return_value=MP3)) as mock:
            asyncio.run(speech.audio('apple'))
            self.assertEqual(mock.await_args.args[1], 'azure')
            asyncio.run(speech.audio('apple'))
            self.assertEqual(mock.await_count, 1)
            settings = speech.load()
            settings.azure.voice = 'en-GB-SoniaNeural'
            speech.save(settings)
            asyncio.run(speech.audio('apple'))
            self.assertEqual(mock.await_count, 2)
        speech.clear_cache()
        with patch.object(speech, 'synthesize', AsyncMock(side_effect=httpx.ReadTimeout('timeout'))):
            with self.assertRaises(HTTPException):
                asyncio.run(speech.audio('apple'))
        self.assertEqual(speech.cache_stats()['files'], 0)

    def test_provider_requests_and_response_bounds(self):
        requests = []

        def handler(request):
            requests.append(request)
            if 'googleapis' in request.url.host:
                return httpx.Response(200, json={'audioContent': base64.b64encode(MP3).decode()})
            return httpx.Response(200, content=MP3, headers={'content-type': 'audio/mpeg'})

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                for name in ('azure', 'google'):
                    self.assertEqual(await speech.synthesize(client, name, getattr(self.configured(), name), '<apple & pear>'), MP3)
        asyncio.run(run())
        self.assertIn(b'&lt;apple &amp; pear&gt;', requests[0].content)
        self.assertNotIn('google-secret', str(requests[1].url))
        self.assertEqual(json.loads(requests[1].content)['input']['text'], '<apple & pear>')

        async def oversized():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'x' * 21, headers={'content-type': 'audio/mpeg'}))) as client:
                await speech.synthesize(client, 'azure', self.configured().azure, 'apple')
        with patch.object(speech, 'MAX_AUDIO', 20), self.assertRaises(ValueError):
            asyncio.run(oversized())


if __name__ == '__main__':
    unittest.main()

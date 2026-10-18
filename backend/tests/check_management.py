"""运行：python -m unittest backend.tests.check_management -v"""
from backend.tests.check_core import CoreChecks
from backend.app.db import connect


class ManagementChecks(CoreChecks):
    def test_preview_duplicates_management_and_reset(self):
        items = [{'word': 'Hello', 'meaning': '你好'}, {'word': ' hello ', 'meaning': '喂'}]
        preview = self.post('/api/import/preview', {'items': items})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['duplicates'][0]['indices'], [0, 1])
        self.assertEqual(self.post('/api/decks/import', {'items': items}).status_code, 422)
        deck = self.make_deck()
        details = self.client.get(f'/api/decks/{deck}').json()
        wid = details['words'][0]['id']
        self.assertEqual(self.client.patch(f'/api/decks/{deck}', json={'name': '新词库'}, headers=self.headers).status_code, 200)
        exported = self.client.get(f'/api/decks/{deck}/export').json()
        self.assertEqual(len(exported), 2)
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        current = self.client.get(f'/api/study/{sid}/next').json()['item']
        self.post(f'/api/study/{sid}/items/{current["id"]}/answer', {'skip': True})
        self.assertEqual(self.post(f'/api/words/{wid}/reset').status_code, 200)
        self.assertEqual(self.client.get(f'/api/study/{sid}/next').status_code, 409)
        history = self.client.get(f'/api/words/{wid}/history').json()
        self.assertEqual(history['attempts'], [])
        self.assertEqual(history['stage'], -1)
        self.assertEqual(self.client.patch(f'/api/words/{wid}', json={'meaning': '问候'}, headers=self.headers).status_code, 200)
        self.login('bob')
        for path in [f'/api/decks/{deck}', f'/api/decks/{deck}/export', f'/api/words/{wid}/history']:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.delete(f'/api/decks/{deck}', headers=self.headers).status_code, 404)
        self.login('alice')
        for word in self.client.get(f'/api/decks/{deck}').json()['words']:
            self.assertEqual(self.client.delete(f'/api/words/{word["id"]}', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/api/decks').json()[0]['total'], 0)
        self.assertEqual(self.client.delete(f'/api/decks/{deck}', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/api/decks').json(), [])
        with connect() as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_delete_deck_with_active_batch(self):
        deck = self.make_deck()
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        self.assertEqual(self.client.delete(f'/api/decks/{deck}', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get(f'/api/study/{sid}/next').status_code, 409)
        with connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM study_items').fetchone()[0], 0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

"""运行：.venv/bin/python -m unittest backend.tests.check_statistics -v"""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend.tests import check_core
from backend.app.db import connect


class StatisticsChecks(unittest.TestCase):
    setUp = check_core.CoreChecks.setUp
    login = check_core.CoreChecks.login
    post = check_core.CoreChecks.post
    make_deck = check_core.CoreChecks.make_deck

    def statistics(self, query: str = '') -> dict:
        response = self.client.get('/api/statistics' + query)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_empty_validation_and_ownership(self) -> None:
        result = self.statistics()
        self.assertEqual(result['totals']['total'], 0)
        self.assertEqual(result['mistakes'], [])
        self.assertEqual(len(result['daily']), 7)
        self.assertEqual(result['first_round']['new']['accuracy'], None)
        for query in ('?range=1', '?range=week', '?deck_id=bad'):
            self.assertEqual(self.client.get('/api/statistics' + query).status_code, 422)
        deck = self.make_deck()
        self.login('bob')
        self.assertEqual(self.client.get(f'/api/statistics?deck_id={deck}').status_code, 404)
        self.assertEqual(self.statistics()['decks'], [])
        self.login('parent')
        self.assertEqual(self.client.get(f'/api/statistics?deck_id={deck}').status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/statistics').status_code, 401)

    def test_ranges_decks_daily_and_due(self) -> None:
        now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc).timestamp()
        first, second = self.make_deck(), self.make_deck()
        with connect() as db:
            uid = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            words = [r[0] for r in db.execute('SELECT id FROM words ORDER BY id')]
            for wid, stage, due in zip(words, (0, 2, -1, 5), (now - 86400, now + 86400, None, now + 7 * 86400)):
                db.execute('UPDATE words SET stage=?,due_at=? WHERE id=?', (stage, due, wid))
            for kind, did, entries in (
                ('new', first, [(words[0], 1, 0, now - 3600), (words[0], 2, 1, now - 1800),
                                (words[1], 1, 1, now - 10 * 86400)]),
                ('review', None, [(words[0], 1, 0, now - 31 * 86400), (words[3], 1, 1, now - 60)]),
            ):
                sid = db.execute("INSERT INTO study_sessions(user_id,deck_id,kind,status) VALUES(?,?,?,'complete')",
                                 (uid, did, kind)).lastrowid
                for position, (wid, number, correct, timestamp) in enumerate(entries):
                    iid = db.execute('INSERT INTO study_items(session_id,word_id,round_number,position) VALUES(?,?,?,?)',
                                     (sid, wid, number, position)).lastrowid
                    db.execute('INSERT INTO attempts(item_id,answer,correct,created_at) VALUES(?,?,?,?)',
                               (iid, 'answer', correct, timestamp))
        with patch('backend.app.management.time.time', return_value=now):
            result = self.statistics()
            self.assertEqual(result['totals'], {'total': 4, 'passed': 3, 'due': 1,
                                               'stages': {'-1': 1, '0': 1, '1': 0, '2': 1, '3': 0, '4': 0, '5': 1}})
            self.assertEqual(result['first_round']['new'], {'attempts': 1, 'correct': 0, 'accuracy': 0.0})
            self.assertEqual(result['first_round']['review']['accuracy'], 1.0)
            self.assertEqual(result['daily'][-1], {'date': '2026-10-20', 'attempts': 3, 'correct': 2, 'new_passed': None})
            self.assertFalse(result['new_passed_available'])
            self.assertTrue(result['new_passed_note'])
            self.assertEqual([day['count'] for day in result['upcoming_due']], [0, 1, 0, 0, 0, 0, 0])
            mistake = result['mistakes'][0]
            self.assertEqual((mistake['word_id'], mistake['wrong'], mistake['attempts'], mistake['error_rate']),
                             (words[0], 1, 2, 0.5))
            self.assertEqual(mistake['last_wrong_at'], now - 3600)
            self.assertEqual(mistake['deck_name'], result['decks'][0]['name'])
            filtered = self.statistics(f'?deck_id={second}&range=all')
            self.assertEqual(filtered['totals']['total'], 2)
            self.assertEqual(filtered['mistakes'], [])
            self.assertEqual(filtered['first_round']['review']['attempts'], 1)
            self.assertEqual(sum(day['attempts'] for day in filtered['daily']), 1)
            month = self.statistics('?range=30')
            self.assertEqual(len(month['daily']), 30)
            self.assertEqual(sum(day['attempts'] for day in month['daily']), 4)
            all_time = self.statistics('?range=all')
            self.assertIsNone(all_time['since'])
            self.assertEqual(sum(day['attempts'] for day in all_time['daily']), 5)
            self.assertEqual(all_time['mistakes'][0]['wrong'], 2)
            self.assertAlmostEqual(all_time['mistakes'][0]['error_rate'], 2 / 3)

    def test_practice_replay_and_unanswered_spelling(self) -> None:
        deck = self.make_deck()
        sid = self.post(f'/api/decks/{deck}/study').json()['id']
        self.assertEqual(self.statistics()['mistakes'], [])
        item = self.client.get(f'/api/study/{sid}/next').json()['item']
        path = f'/api/study/{sid}/items/{item["id"]}'
        spelling = self.post(path + '/reveal').json()['word']
        self.post(path + '/answer', {'skip': True})
        for _ in range(2):
            self.post(path + '/answer', {'answer': spelling, 'practice': True})
        self.post(path + '/answer', {'skip': True})
        result = self.statistics('?range=all')
        self.assertEqual(len(result['mistakes']), 1)
        self.assertEqual(result['mistakes'][0]['word'], spelling)
        self.assertEqual(result['mistakes'][0]['wrong'], 1)
        self.assertEqual(result['mistakes'][0]['attempts'], 1)
        self.assertEqual(result['first_round']['new']['attempts'], 1)
        self.assertEqual(sum(day['attempts'] for day in result['daily']), 1)
        self.assertNotIn('answer', str(result))


if __name__ == '__main__':
    unittest.main()

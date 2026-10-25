"""FSRS-6 adapter; exposure time is not review time."""
import json
import math
from datetime import datetime, timezone

from fsrs import Card, Rating, Scheduler, State

SCHEDULER = Scheduler(desired_retention=0.9, maximum_interval=30, enable_fuzzing=False)


def load_card(value: str) -> Card:
    """Validate untrusted serialized cards before calling permissive library decoder."""
    try:
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError('FSRS card size')
        data = json.loads(value)
        if not isinstance(data, dict) or set(data) != {'card_id', 'state', 'step', 'stability', 'difficulty', 'due', 'last_review'}:
            raise ValueError('FSRS card fields')
        if type(data['card_id']) is not int or not 1 <= data['card_id'] <= 9223372036854775807:
            raise ValueError('FSRS card id')
        if type(data['state']) is not int or data['state'] not in (1, 2, 3):
            raise ValueError('FSRS state')
        step = data['step']
        if data['state'] == State.Review.value:
            if step is not None:
                raise ValueError('FSRS review step')
        elif type(step) is not int or not 0 <= step < (2 if data['state'] == State.Learning.value else 1):
            raise ValueError('FSRS learning step')
        for field, low, high in [('stability', 0, 36500), ('difficulty', 1, 10)]:
            number = data[field]
            if type(number) not in (int, float) or not math.isfinite(number) or not low <= number <= high or number <= 0:
                raise ValueError('FSRS parameter')
        for field in ('due', 'last_review'):
            text = data[field]
            if not isinstance(text, str) or len(text) > 40:
                raise ValueError('FSRS time')
            date = datetime.fromisoformat(text)
            if date.tzinfo != timezone.utc or not 0 <= date.timestamp() <= 253402300799:
                raise ValueError('FSRS UTC time')
        card = Card.from_json(value)
        if card.due <= card.last_review:
            raise ValueError('FSRS due precedes review')
        return card
    except (TypeError, KeyError, OverflowError, ValueError) as exc:
        raise ValueError('FSRS 卡片无效') from exc


def review(value: str | None, word_id: int, correct: bool, effort: str, now: float, last_seen: float | None) -> Card | None:
    at = datetime.fromtimestamp(now, timezone.utc)
    if correct and last_seen is not None and now - last_seen < 600:
        return None
    card = load_card(value) if value is not None else Card(card_id=word_id, due=at)
    # ponytail: early successes require 10 minutes without exposure; tune only with recall evidence.
    if correct and card.last_review is not None:
        elapsed = now - card.last_review.timestamp()
        independent = last_seen is None or now - last_seen >= 600
        if elapsed <= 0 or not independent or (at < card.due and elapsed < 600):
            return None
    if card.last_review is not None and at < card.last_review:
        # Clock rollback must not produce negative FSRS elapsed time.
        at = card.last_review
    rating = Rating.Again if not correct else Rating.Hard if effort == 'hard' else Rating.Good
    updated, _ = SCHEDULER.review_card(card, rating, review_datetime=at)
    load_card(updated.to_json())
    return updated

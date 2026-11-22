"""Pure reads over the answer bank and its form references."""
from __future__ import annotations


def forms(fb):
    for url, item in fb.items():
        form = item.get('form') if isinstance(item, dict) else None
        if form:
            yield url, form


def users(fb, key):
    return {url: bool(form.get('lock')) for url, form in forms(fb)
            if any(field.get('k') == key for field in form.get('f', []))}


def empty_answer(answer):
    blank = lambda value: not str(value or '').replace('\ufeff', '').strip()
    return blank(answer.get('v')) and blank(answer.get('zh'))


def waiting(fb):
    out = []
    for answer in fb.get('__ans__') or []:
        if answer.get('redo'):
            continue
        linked = users(fb, answer['k'])
        if answer.get('inf') or (empty_answer(answer) and (not linked or not all(linked.values()))):
            out.append((answer, sorted(linked)))
    return out


def find_shared(fb):
    return [answer for answer in fb.get('__ans__') or []
            if not answer.get('pj') and not answer.get('platform')
            and not answer.get('inf') and not empty_answer(answer)]

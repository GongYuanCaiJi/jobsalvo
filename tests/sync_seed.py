"""測試與考試的準備步驟:替一份登記好的平台履歷放一份「已看過」的版本。產品程式不呼叫它。"""
from unittest import mock

import profile_sync as ps
import sync
import sync_material


@ps._registry_writer
def seed_sync_baseline(platform, lang, variant, source, answers, master_fp=None, attachments=None):
    reg = ps._load_registry()
    entry = (reg.get(platform) or {}).get(f'{lang}/{variant}')
    if not isinstance(entry, dict):
        raise ValueError('平台履歷未登記')
    slot = sync.slot_of(platform, lang, variant, entry)
    with mock.patch.object(sync_material, 'shared_answers', return_value=({}, [])):   # 只要附件與指紋,不需要看板
        current = sync_material.source(slot)
    text = {}
    for part in variant.split('+'):
        for where, body, _links, _value, _labels in sync_material.md_sections(str(source)):
            text[f'{part}: {where}'] = body
    text['附件'] = str(attachments if attachments is not None else current.get('附件', ''))
    for answer in answers or []:
        if isinstance(answer, dict):
            text[f"答案: {answer.get('q') or answer.get('k') or ''}"] = str(answer.get('v') or '')
    text['__target_fingerprint__'] = (current.get('__target_fingerprint__', '')
                                      if master_fp in (None, ps._master_fp(lang, variant))
                                      else str(master_fp))
    entry['sync_versions'] = [{'round_id': 'seed', 'target': sync.target(text), 'target_text': text,
                               'reviewed_at': 'seed'}]
    entry['sync_version'] = 'seed'
    for key in ('sync_baseline', 'sync_pending_baseline', 'sync_reviewed_version'):
        entry.pop(key, None)
    ps._save_registry(reg)

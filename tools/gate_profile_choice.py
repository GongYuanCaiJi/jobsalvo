"""投遞前:agent 走到真正的申請表,投遞方式由程式讀那一頁自己算(不問 agent)。"""
from gate_cells import CHECK, Cell
from profile_sync import profile_field


def method_of(page):
    """申請頁實際怎麼交履歷:有上傳欄就直接上傳;沒有、但有平台履歷選項就用平台履歷;都沒有就不用履歷。"""
    fields = [field for field in (page or {}).get('fields') or [] if isinstance(field, dict)]
    if any(str(field.get('type') or '').lower() == 'file' for field in fields):
        return 'direct_upload'
    if (page or {}).get('profileLinks') or any(profile_field(field) for field in fields):
        return 'platform_profile'
    return 'no_profile'


def _tab(value, _sheet, truth):
    return None if str(value or '') == str(truth.tab_id or '') else '不是這張卡工作區裡讀到的分頁'


PROFILE_CHOICE = {
    'tab_id': Cell(CHECK, '申請頁分頁', '這張卡工作區裡程式讀到的分頁', _tab,
                   required='沒記下申請頁分頁'),
}

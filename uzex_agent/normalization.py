"""Explicit aliases, not fuzzy matching. Product grades and sizes stay distinct."""
import re
import unicodedata
from functools import lru_cache


def clean(value):
    value = unicodedata.normalize('NFKC', value).casefold().replace('ё', 'е')
    value = re.sub('[‘’ʻʼ`´]', "'", value)
    value = re.sub(r'[-‐‑–—]', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


REGIONS = {
    'Андижанская область': ['andijon viloyati', 'andijan region', 'андижон вилояти'],
    'Бухарская область': ['buxoro viloyati', 'bukhara region', 'бухоро вилояти'],
    'Джизакская область': ['jizzax viloyati', 'jizzakh region', 'жиззах вилояти'],
    'Кашкадарьинская область': ['qashqadaryo viloyati', 'kashkadarya region', 'қашқадарё вилояти'],
    'Навоийская область': ['navoiy viloyati', 'navoi region', 'навоий вилояти'],
    'Наманганская область': ['namangan viloyati', 'namangan region', 'наманган вилояти'],
    'Республика Каракалпакстан': ["qoraqalpog'iston respublikasi", 'karakalpakstan', 'қорақалпоғистон республикаси'],
    'Самаркандская область': ['samarqand viloyati', 'samarkand region', 'самарқанд вилояти'],
    'Сурхандарьинская область': ['surxondaryo viloyati', 'surkhandarya region', 'сурхондарё вилояти'],
    'Сырдарьинская область': ['sirdaryo viloyati', 'syrdarya region', 'сирдарё вилояти'],
    'Ташкентская область': ['toshkent viloyati', 'tashkent region', 'тошкент вилояти'],
    'г. Ташкент': ['toshkent shahri', 'tashkent city', 'город ташкент', 'тошкент шаҳри'],
    'Ферганская область': ["farg'ona viloyati", 'fergana region', 'фарғона вилояти'],
    'Хорезмская область': ['xorazm viloyati', 'khorezm region', 'хоразм вилояти'],
}
REGION_ALIASES = {clean(alias): label for label, aliases in REGIONS.items() for alias in [label, *aliases]}
PRODUCT_ALIASES = {
    'цемент': ['портландцемент', 'цемент портландский', 'sement', 'portlandsement'],
    'бензин': ['бензин автомобильный', 'benzin'],
    'дизельное топливо': ['дизтопливо', 'dizel yoqilgisi', "dizel yoqilg'isi"],
    'карбамид': ['мочевина (карбамид)', 'мочевина', 'karbamid'],
    'мука': ['un'],
    'пшеница': ["bug'doy", 'bugdoy'],
    'рис': ['guruch'],
    'сахар': ['shakar'],
    'хлопок сырец': ['paxta xom ashyosi', 'paxta xomashyosi'],
    'масло хлопковое': ['хлопковое масло', 'paxta yogi', "paxta yog'i"],
    'масло подсолнечное': ['подсолнечное масло', "kungaboqar yog'i"],
    'шрот хлопковый': ['шрот хлопковых семян'],
    'сжиженный газ': ['газ сжиженный пропан бутан', 'suyultirilgan gaz'],
    'арматура': ['арматура стальная', 'armatura'],
}
REPLACEMENTS = sorted([(clean(alias), clean(label)) for label, aliases in PRODUCT_ALIASES.items() for alias in aliases], key=lambda x: -len(x[0]))
PATTERNS = [(re.compile(r'(?<!\w)' + re.escape(alias) + r'(?!\w)'), label) for alias, label in REPLACEMENTS]


@lru_cache(maxsize=30000)
def product(value):
    value = clean(value)
    for pattern, replacement in PATTERNS:
        value = pattern.sub(replacement, value)
    for number, words in [('1', 'первый|первого'), ('2', 'второй|второго'), ('3', 'третий|третьего')]:
        value = re.sub(r'\b(?:' + words + r') сорт(?:а)?\b', 'сорт ' + number, value)
    value = re.sub(r'\b([123])\s*(?:го\s*)?сорт(?:а)?\b', r'сорт \1', value)
    value = re.sub(r'\bсорт\s+сорт\b', 'сорт', value)
    return value


@lru_cache(maxsize=1000)
def region(value):
    return REGION_ALIASES.get(clean(value), clean(value))


def notes():
    return 'Регионы и известные синонимы товаров объединены по явному словарю; марки, размеры и сорта не удаляются. Неизвестные написания не угадываются.'

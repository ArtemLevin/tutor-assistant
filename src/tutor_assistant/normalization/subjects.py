from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class SubjectProfileName(StrEnum):
    MATHEMATICS = "mathematics"
    PHYSICS = "physics"
    CHEMISTRY = "chemistry"
    GENERIC = "generic"


COMMON_PROTECTED_STEMS = (
    "задач",
    "услов",
    "решен",
    "ответ",
    "домашн",
    "упражнен",
    "пример",
    "формул",
    "определен",
    "вывод",
)

COMMON_CURRICULUM_TERMS = (
    "условие задачи, решение, ответ, объяснение, определение, правило, формула, "
    "пример, упражнение и домашнее задание",
)


@dataclass(frozen=True, slots=True)
class SubjectProfile:
    name: SubjectProfileName
    display_name: str
    prompt_version: str
    aliases: tuple[str, ...]
    curriculum_terms: tuple[str, ...]
    protected_stems: tuple[str, ...]
    unit_tokens: tuple[str, ...] = ()
    formula_examples: tuple[str, ...] = ()

    @property
    def all_protected_stems(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*COMMON_PROTECTED_STEMS, *self.protected_stems)))

    @property
    def prompt_terms(self) -> str:
        return "; ".join((*COMMON_CURRICULUM_TERMS, *self.curriculum_terms))

    @property
    def prompt_units(self) -> str:
        return ", ".join(self.unit_tokens) or "предметные единицы измерения из исходного текста"

    @property
    def prompt_formulas(self) -> str:
        return ", ".join(self.formula_examples) or "формулы и обозначения из исходного текста"


SUBJECT_PROFILES: dict[SubjectProfileName, SubjectProfile] = {
    SubjectProfileName.MATHEMATICS: SubjectProfile(
        name=SubjectProfileName.MATHEMATICS,
        display_name="Математика",
        prompt_version="educational-content-filter.mathematics.v2",
        aliases=(
            "mathematics",
            "math",
            "maths",
            "математика",
            "алгебра",
            "геометрия",
            "планиметрия",
            "стереометрия",
            "огэ математика",
            "егэ математика",
        ),
        curriculum_terms=(
            "натуральные, целые, рациональные и действительные числа, дроби, проценты и пропорции",
            "степени, корни, модуль, одночлены, многочлены и разложение на множители",
            "уравнения, неравенства, системы, совокупности, ОДЗ, дискриминант и метод интервалов",
            "функции, графики, область определения, монотонность, экстремумы, производная и интеграл",
            "арифметическая и геометрическая прогрессии, логарифмы и тригонометрия",
            "планиметрия, стереометрия, теоремы, доказательства, площади, объёмы и векторы",
            "комбинаторика, вероятность, статистика, текстовые задачи, движение, смеси и кредиты",
            "ОГЭ, ЕГЭ, номер задания и промежуточные вычисления",
        ),
        protected_stems=(
            "логариф",
            "неравен",
            "уравнен",
            "функц",
            "график",
            "производн",
            "интеграл",
            "первообразн",
            "прогресси",
            "тригонометр",
            "синус",
            "косинус",
            "тангенс",
            "котангенс",
            "дискриминант",
            "корень",
            "степен",
            "модул",
            "одз",
            "интервал",
            "теорем",
            "доказатель",
            "треуголь",
            "четырехуголь",
            "окружност",
            "площад",
            "объем",
            "вектор",
            "координат",
            "вероятност",
            "комбинатор",
            "статистик",
            "радиан",
            "градус",
        ),
        unit_tokens=(
            "градус",
            "радиан",
            "миллиметр",
            "сантиметр",
            "метр",
            "квадратный сантиметр",
            "квадратный метр",
            "кубический сантиметр",
            "кубический метр",
        ),
        formula_examples=("x²", "√x", "logₐx", "sin x", "f′(x)", "Sₙ"),
    ),
    SubjectProfileName.PHYSICS: SubjectProfile(
        name=SubjectProfileName.PHYSICS,
        display_name="Физика",
        prompt_version="educational-content-filter.physics.v1",
        aliases=(
            "physics",
            "физика",
            "механика",
            "молекулярная физика",
            "термодинамика",
            "электродинамика",
            "оптика",
            "квантовая физика",
            "астрономия",
            "огэ физика",
            "егэ физика",
        ),
        curriculum_terms=(
            "кинематика, путь, перемещение, скорость, ускорение и графики движения",
            "динамика, масса, сила, законы Ньютона, импульс, работа, энергия и мощность",
            "давление, плотность, гидростатика, молекулярная физика и термодинамика",
            "электрический заряд, ток, напряжение, сопротивление, закон Ома и электрические цепи",
            "магнитное поле, электромагнитная индукция, колебания, волны, звук и резонанс",
            "геометрическая и волновая оптика, линзы, фокус, квант, фотон и радиоактивность",
            "лабораторные измерения, погрешности, единицы СИ и перевод единиц",
        ),
        protected_stems=(
            "скорост",
            "ускорен",
            "перемещ",
            "траектор",
            "масса",
            "сил",
            "ньютон",
            "импульс",
            "энерг",
            "мощност",
            "давлен",
            "плотност",
            "температур",
            "теплот",
            "термодинами",
            "электр",
            "заряд",
            "напряжен",
            "сопротивлен",
            "магнит",
            "индукц",
            "колебан",
            "частот",
            "период",
            "волна",
            "резонанс",
            "оптик",
            "линз",
            "фокус",
            "квант",
            "фотон",
            "радиоактив",
            "полураспад",
            "погрешност",
        ),
        unit_tokens=(
            "м/с",
            "м/с²",
            "м/с^2",
            "кг",
            "н",
            "дж",
            "вт",
            "па",
            "кл",
            "а",
            "в",
            "ом",
            "тл",
            "гц",
            "к",
            "°c",
            "ньютон",
            "джоуль",
            "ватт",
            "паскаль",
            "кулон",
            "ампер",
            "вольт",
            "тесла",
            "герц",
            "кельвин",
            "градус цельсия",
        ),
        formula_examples=("F = ma", "p = mv", "E = mc²", "I = U/R", "Q = cmΔT", "ν = 1/T"),
    ),
    SubjectProfileName.CHEMISTRY: SubjectProfile(
        name=SubjectProfileName.CHEMISTRY,
        display_name="Химия",
        prompt_version="educational-content-filter.chemistry.v1",
        aliases=(
            "chemistry",
            "химия",
            "organic chemistry",
            "inorganic chemistry",
            "органическая химия",
            "неорганическая химия",
            "общая химия",
            "огэ химия",
            "егэ химия",
        ),
        curriculum_terms=(
            "атом, молекула, химический элемент, простые и сложные вещества и периодический закон",
            "химическая связь, валентность, степень окисления и строение вещества",
            "химические реакции, коэффициенты, количество вещества, молярная масса и объём",
            "растворы, концентрация, электролиты, диссоциация, гидролиз и ионные уравнения",
            "оксиды, кислоты, основания, соли, окислители, восстановители и ОВР",
            "органическая химия, углеводороды, алканы, алкены, алкины, арены и изомерия",
            "спирты, фенолы, альдегиды, кетоны, карбоновые кислоты, эфиры и полимеры",
            "качественные реакции, цепочки превращений, выход продукта и расчёты по уравнению",
        ),
        protected_stems=(
            "атом",
            "молекул",
            "элемент",
            "веществ",
            "реакц",
            "коэффициент",
            "моль",
            "моляр",
            "валентност",
            "окислен",
            "восстанов",
            "кислот",
            "основан",
            "соль",
            "оксид",
            "гидроксид",
            "электролит",
            "диссоциац",
            "гидролиз",
            "органическ",
            "углеводород",
            "алкан",
            "алкен",
            "алкин",
            "арен",
            "спирт",
            "фенол",
            "альдегид",
            "кетон",
            "карбонов",
            "эфир",
            "изомер",
            "гомолог",
            "полимер",
            "белок",
            "углевод",
            "раствор",
            "концентрац",
        ),
        unit_tokens=(
            "моль",
            "г/моль",
            "моль/л",
            "г",
            "кг",
            "л",
            "мл",
            "кдж",
            "°c",
            "литр",
            "грамм",
            "килограмм",
            "килоджоуль",
            "градус цельсия",
        ),
        formula_examples=("H₂SO₄", "Ca(OH)₂", "n = m/M", "C = n/V", "CH₄ + 2O₂ → CO₂ + 2H₂O"),
    ),
    SubjectProfileName.GENERIC: SubjectProfile(
        name=SubjectProfileName.GENERIC,
        display_name="Общий учебный профиль",
        prompt_version="educational-content-filter.generic.v1",
        aliases=("generic", "general", "общий", "другое", "other"),
        curriculum_terms=(
            "термины, определения, факты, аргументы, причинно-следственные связи и выводы",
            "вопросы и ответы ученика, исправления, затруднения и учебные инструкции",
        ),
        protected_stems=(),
    ),
}


def normalize_subject_key(value: str | None) -> str:
    normalized = (value or "").casefold().replace("ё", "е")
    normalized = re.sub(r"[_/\\|:;,+-]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def resolve_subject_profile(subject: str | SubjectProfileName | None) -> SubjectProfile:
    if isinstance(subject, SubjectProfileName):
        return SUBJECT_PROFILES[subject]
    normalized = normalize_subject_key(subject)
    if not normalized:
        return SUBJECT_PROFILES[SubjectProfileName.GENERIC]
    for name in SubjectProfileName:
        if normalized == name.value:
            return SUBJECT_PROFILES[name]
    padded = f" {normalized} "
    for name in (
        SubjectProfileName.CHEMISTRY,
        SubjectProfileName.PHYSICS,
        SubjectProfileName.MATHEMATICS,
    ):
        profile = SUBJECT_PROFILES[name]
        for alias in sorted(profile.aliases, key=len, reverse=True):
            normalized_alias = normalize_subject_key(alias)
            if normalized == normalized_alias or f" {normalized_alias} " in padded:
                return profile
    return SUBJECT_PROFILES[SubjectProfileName.GENERIC]


def get_subject_profile(value: str | SubjectProfileName | None) -> SubjectProfile:
    return resolve_subject_profile(value)


def prompt_version_for_subject(subject: str | SubjectProfileName | None) -> str:
    return resolve_subject_profile(subject).prompt_version

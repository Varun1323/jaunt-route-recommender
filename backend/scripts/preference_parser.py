"""Preference parsing module for Jaunt walking route recommendation.

Extracts structured traveler preferences from natural language free-text queries,
supporting dimensions such as architecture/history interest, shortest distance,
minimum walking time, landmark count preference, and time constraints.
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserPreferences:
    """Structured representation of traveler route preferences."""
    architecture: float = 0.0
    history: float = 0.0
    shortest_distance: float = 0.0
    minimum_walking_time: float = 0.0
    more_landmarks: float = 0.0
    min_landmarks: int = 0
    walking_time_budget_minutes: float | None = None
    max_duration_minutes: float | None = None
    max_distance_m: float | None = None
    max_detour_ratio: float | None = None
    excluded_categories: list[str] = field(default_factory=list)
    preferred_categories: list[str] = field(default_factory=list)
    primary_interest: str | None = None
    secondary_interests: list[str] = field(default_factory=list)
    requested_landmark_ids: list[str] = field(default_factory=list)
    preferred_landmark_ids: list[str] = field(default_factory=list)
    sightseeing_waypoint_ids: list[str] = field(default_factory=list)
    additional_sightseeing_sequences: list[list[str]] = field(default_factory=list)
    requested_route_count: int = 1
    route_priority: str = "balanced"
    is_qualitative_time_constrained: bool = False
    raw_query: str = ""
    unsupported_preferences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture": round(self.architecture, 2),
            "history": round(self.history, 2),
            "shortest_distance": round(self.shortest_distance, 2),
            "minimum_walking_time": round(self.minimum_walking_time, 2),
            "more_landmarks": round(self.more_landmarks, 2),
            "min_landmarks": self.min_landmarks,
            "walking_time_budget_minutes": self.walking_time_budget_minutes,
            "max_duration_minutes": self.max_duration_minutes,
            "max_distance_m": self.max_distance_m,
            "max_detour_ratio": self.max_detour_ratio,
            "excluded_categories": self.excluded_categories,
            "preferred_categories": self.preferred_categories,
            "primary_interest": self.primary_interest,
            "secondary_interests": self.secondary_interests,
            "requested_landmark_ids": self.requested_landmark_ids,
            "preferred_landmark_ids": self.preferred_landmark_ids,
            "sightseeing_waypoint_ids": self.sightseeing_waypoint_ids,
            "additional_sightseeing_sequences": self.additional_sightseeing_sequences,
            "requested_route_count": self.requested_route_count,
            "route_priority": self.route_priority,
            "is_qualitative_time_constrained": self.is_qualitative_time_constrained,
            "raw_query": self.raw_query,
            "unsupported_preferences": self.unsupported_preferences,
        }


class PreferenceValidationError(ValueError):
    """Raised when structured preference data does not match the canonical schema."""


PREFERENCE_PAYLOAD_FIELDS = {
    "architecture",
    "history",
    "shortest_distance",
    "minimum_walking_time",
    "more_landmarks",
    "min_landmarks",
    "walking_time_budget_minutes",
    "max_duration_minutes",
    "max_distance_m",
    "max_detour_ratio",
    "excluded_categories",
    "preferred_categories",
    "primary_interest",
    "secondary_interests",
    "requested_landmark_ids",
    "preferred_landmark_ids",
    "sightseeing_waypoint_ids",
    "additional_sightseeing_sequences",
    "requested_route_count",
    "route_priority",
    "is_qualitative_time_constrained",
    "unsupported_preferences",
}


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PreferenceValidationError(
            f"Canonical preference '{field_name}' must be a number."
        )
    result = float(value)
    if not math.isfinite(result):
        raise PreferenceValidationError(
            f"Canonical preference '{field_name}' must be finite."
        )
    return result


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PreferenceValidationError(
            f"Canonical preference '{field_name}' must be a list of strings."
        )
    return list(value)


def validate_structured_preferences(
    payload: Any,
    available_landmarks: list[dict[str, Any]] | None = None,
    raw_query: str = "",
) -> UserPreferences:
    """Validate provider or other structured intent and return canonical UserPreferences."""
    if not isinstance(payload, dict):
        raise PreferenceValidationError(
            "Canonical preferences must be a JSON object."
        )

    unknown_fields = set(payload) - PREFERENCE_PAYLOAD_FIELDS
    if unknown_fields:
        raise PreferenceValidationError(
            f"Canonical preferences contain unsupported fields: {sorted(unknown_fields)}"
        )

    normalized = dict(payload)
    normalized.setdefault("architecture", 0.0)
    normalized.setdefault("history", 0.0)
    normalized.setdefault("shortest_distance", 0.0)
    normalized.setdefault("minimum_walking_time", 0.0)
    normalized.setdefault("more_landmarks", 0.0)
    normalized.setdefault("min_landmarks", 0)
    normalized.setdefault("walking_time_budget_minutes", None)
    normalized.setdefault("max_duration_minutes", None)
    normalized.setdefault("max_distance_m", None)
    normalized.setdefault("max_detour_ratio", None)
    normalized.setdefault("excluded_categories", [])
    normalized.setdefault("preferred_categories", [])
    normalized.setdefault("primary_interest", None)
    normalized.setdefault("secondary_interests", [])
    normalized.setdefault("requested_landmark_ids", [])
    normalized.setdefault("preferred_landmark_ids", [])
    normalized.setdefault("sightseeing_waypoint_ids", [])
    normalized.setdefault("additional_sightseeing_sequences", [])
    normalized.setdefault("requested_route_count", 1)
    normalized.setdefault("route_priority", "balanced")
    normalized.setdefault("is_qualitative_time_constrained", False)
    normalized.setdefault("unsupported_preferences", [])

    weights: dict[str, float] = {}
    for field_name in (
        "architecture",
        "history",
        "shortest_distance",
        "minimum_walking_time",
        "more_landmarks",
    ):
        value = _finite_number(normalized[field_name], field_name)
        if not 0.0 <= value <= 1.0:
            raise PreferenceValidationError(
                f"Canonical preference '{field_name}' must be between 0 and 1."
            )
        weights[field_name] = value

    min_landmarks = normalized["min_landmarks"]
    if isinstance(min_landmarks, bool) or not isinstance(min_landmarks, int):
        raise PreferenceValidationError(
            "Canonical preference 'min_landmarks' must be an integer."
        )
    if not 0 <= min_landmarks <= 8:
        raise PreferenceValidationError(
            "Canonical preference 'min_landmarks' must be between 0 and 8."
        )

    nullable_limits: dict[str, float | None] = {}
    for field_name, maximum in (
        ("walking_time_budget_minutes", 1440.0),
        ("max_duration_minutes", 1440.0),
        ("max_distance_m", 100000.0),
        ("max_detour_ratio", 2.0),
    ):
        value = normalized[field_name]
        if value is None:
            nullable_limits[field_name] = None
            continue
        number = _finite_number(value, field_name)
        minimum_inclusive = 1.0 if field_name == "max_detour_ratio" else 0.0
        is_in_range = (
            minimum_inclusive <= number <= maximum
            if field_name == "max_detour_ratio"
            else 0.0 < number <= maximum
        )
        if not is_in_range:
            raise PreferenceValidationError(
                f"Canonical preference '{field_name}' is outside the supported range."
            )
        nullable_limits[field_name] = number

    excluded_categories = _string_list(
        normalized["excluded_categories"], "excluded_categories"
    )
    preferred_categories = _string_list(
        normalized["preferred_categories"], "preferred_categories"
    )
    secondary_interests = _string_list(
        normalized["secondary_interests"], "secondary_interests"
    )
    requested_ids = _string_list(
        normalized["requested_landmark_ids"], "requested_landmark_ids"
    )
    preferred_ids = _string_list(
        normalized["preferred_landmark_ids"], "preferred_landmark_ids"
    )
    sightseeing_ids = _string_list(
        normalized["sightseeing_waypoint_ids"], "sightseeing_waypoint_ids"
    )
    additional_sequences_value = normalized["additional_sightseeing_sequences"]
    if not isinstance(additional_sequences_value, list):
        raise PreferenceValidationError(
            "Canonical preference 'additional_sightseeing_sequences' must be a list."
        )
    additional_sequences: list[list[str]] = []
    for index, sequence in enumerate(additional_sequences_value):
        if not isinstance(sequence, list) or any(
            not isinstance(landmark_id, str) for landmark_id in sequence
        ):
            raise PreferenceValidationError(
                f"Canonical preference additional sequence {index} must be a list of strings."
            )
        if not sequence or len(set(sequence)) != len(sequence):
            raise PreferenceValidationError(
                f"Canonical preference additional sequence {index} must contain unique landmarks."
            )
        additional_sequences.append(list(sequence))

    if len(set(sightseeing_ids)) != len(sightseeing_ids):
        raise PreferenceValidationError(
            "Canonical preference 'sightseeing_waypoint_ids' cannot contain duplicates."
        )
    if len(additional_sequences) > 3:
        raise PreferenceValidationError(
            "Canonical preference cannot request more than three additional sightseeing sequences."
        )

    requested_route_count = normalized["requested_route_count"]
    if (
        isinstance(requested_route_count, bool)
        or not isinstance(requested_route_count, int)
        or not 1 <= requested_route_count <= 4
    ):
        raise PreferenceValidationError(
            "Canonical preference 'requested_route_count' must be an integer from 1 to 4."
        )
    unsupported_preferences = _string_list(
        normalized["unsupported_preferences"], "unsupported_preferences"
    )

    if len(set(requested_ids)) != len(requested_ids):
        raise PreferenceValidationError(
            "Canonical preference 'requested_landmark_ids' cannot contain duplicates."
        )
    if len(set(preferred_ids)) != len(preferred_ids):
        raise PreferenceValidationError(
            "Canonical preference 'preferred_landmark_ids' cannot contain duplicates."
        )

    route_priority = normalized["route_priority"]
    if route_priority not in {"efficiency", "sightseeing", "balanced"}:
        raise PreferenceValidationError(
            "Canonical preference 'route_priority' is invalid."
        )
    qualitative = normalized["is_qualitative_time_constrained"]
    if not isinstance(qualitative, bool):
        raise PreferenceValidationError(
            "Canonical preference 'is_qualitative_time_constrained' must be boolean."
        )

    landmarks = available_landmarks or []
    allowed_ids = {
        landmark["id"]
        for landmark in landmarks
        if isinstance(landmark.get("id"), str)
    }
    for landmark_id in [
        *requested_ids,
        *preferred_ids,
        *sightseeing_ids,
        *(landmark_id for sequence in additional_sequences for landmark_id in sequence),
    ]:
        if landmark_id not in allowed_ids:
            raise PreferenceValidationError(
                "OpenRouter selected a landmark outside the approved catalog."
                if raw_query
                else "Structured preferences selected a landmark outside the approved catalog."
            )

    allowed_categories = {
        category
        for landmark in landmarks
        for category in landmark.get("categories", [])
        if isinstance(category, str)
    }
    allowed_categories.update({"history", "landmarks"})
    category_values = [
        *excluded_categories,
        *preferred_categories,
        *secondary_interests,
    ]
    primary_interest = normalized["primary_interest"]
    if primary_interest is not None:
        if not isinstance(primary_interest, str):
            raise PreferenceValidationError(
                "Canonical preference 'primary_interest' must be a string or null."
            )
        category_values.append(primary_interest)
    if any(category not in allowed_categories for category in category_values):
        raise PreferenceValidationError(
            "OpenRouter selected a category outside the approved catalog."
            if raw_query
            else "Structured preferences selected a category outside the approved catalog."
        )
    if any(category not in {"architecture", "history", "landmarks"} for category in excluded_categories):
        raise PreferenceValidationError(
            "Canonical preference 'excluded_categories' contains an unsupported category."
        )

    explicit_ids = extract_requested_landmark_ids(raw_query, landmarks)
    merged_requested_ids = list(explicit_ids)
    merged_requested_ids.extend(
        landmark_id
        for landmark_id in requested_ids
        if landmark_id not in merged_requested_ids
    )

    return UserPreferences(
        architecture=weights["architecture"],
        history=weights["history"],
        shortest_distance=weights["shortest_distance"],
        minimum_walking_time=weights["minimum_walking_time"],
        more_landmarks=weights["more_landmarks"],
        min_landmarks=min_landmarks,
        walking_time_budget_minutes=nullable_limits["walking_time_budget_minutes"],
        max_duration_minutes=nullable_limits["max_duration_minutes"],
        max_distance_m=nullable_limits["max_distance_m"],
        max_detour_ratio=nullable_limits["max_detour_ratio"],
        excluded_categories=excluded_categories,
        preferred_categories=preferred_categories,
        primary_interest=primary_interest,
        secondary_interests=secondary_interests,
        requested_landmark_ids=merged_requested_ids,
        preferred_landmark_ids=preferred_ids,
        sightseeing_waypoint_ids=sightseeing_ids,
        additional_sightseeing_sequences=additional_sequences,
        requested_route_count=requested_route_count,
        route_priority=route_priority,
        is_qualitative_time_constrained=qualitative,
        raw_query=raw_query,
        unsupported_preferences=unsupported_preferences,
    )


# Regex patterns for explicit hard numeric time limits (e.g., "under 10 minutes", "within 15 mins", "12 min or less", "maximum 20 min")
HARD_TIME_LIMIT_PATTERNS = [
    re.compile(
        r"\b(?:keep\s+(?:the\s+walk\s+)?under|under|within|less\s+than|max|maximum|at\s+most|no\s+more\s+than|up\s+to|do\s+not\s+exceed|don't\s+exceed)\s+"
        r"(\d+(?:\.\d+)?)\s*(?:min|minute|minutes|m\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d+(?:\.\d+)?)\s*(?:min|minute|minutes)\s*(?:or\s+less|max|maximum)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d+(?:\.\d+)?)\s*(?:min|minute|minutes)\s+(?:maximum|cap|ceiling|limit)\b",
        re.IGNORECASE,
    ),
]

# Regex patterns for available walking-time budget (e.g., "have 20 minutes", "can spend around 30 minutes", "willing to walk for 45 minutes")
TIME_BUDGET_PATTERNS = [
    re.compile(
        r"\b(?:have|got|spend|can\s+spend|willing\s+to\s+walk(?:\s+for)?|plan\s+(?:something\s+)?for)\s+"
        r"(?:around\s+|about\s+|approx(?:\.|imately)?\s+)?(\d+(?:\.\d+)?)\s*(?:min|minute|minutes)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:time\s+budget\s+(?:of\s+)?|budget\s+(?:of\s+)?)(\d+(?:\.\d+)?)\s*(?:min|minute|minutes)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d+(?:\.\d+)?)\s*(?:min|minute|minutes)\s+(?:to\s+(?:explore|walk|wander|spend|see)|budget)\b",
        re.IGNORECASE,
    ),
]

NUMERIC_TIME_PATTERNS = HARD_TIME_LIMIT_PATTERNS

# Qualitative time-constraint phrases (e.g. "don't have much time", "in a rush")
QUALITATIVE_TIME_PHRASES = [
    "don't have much time",
    "do not have much time",
    "don't have a lot of time",
    "do not have a lot of time",
    "don't have many minutes",
    "do not have many minutes",
    "don't have enough time",
    "do not have enough time",
    "not much time",
    "not a lot of time",
    "no time to lose",
    "no time to waste",
    "no time to spare",
    "have no time",
    "little time",
    "short on time",
    "short of time",
    "in a rush",
    "in a hurry",
    "pressed for time",
    "tight schedule",
    "limited time",
    "running late",
    "running short on time",
    "time is limited",
    "time is short",
]

# Patterns representing legitimate limited-time expressions (positive time constraints)
LIMITED_TIME_PATTERNS = [
    r"\bdon't\s+have\s+much\s+time\b",
    r"\bdo\s+not\s+have\s+much\s+time\b",
    r"\bdon't\s+have\s+a\s+lot\s+of\s+time\b",
    r"\bdo\s+not\s+have\s+a\s+lot\s+of\s+time\b",
    r"\bdon't\s+have\s+enough\s+time\b",
    r"\bdo\s+not\s+have\s+enough\s+time\b",
    r"\bdon't\s+have\s+many\s+minutes\b",
    r"\bdo\s+not\s+have\s+many\s+minutes\b",
    r"\bnot\s+much\s+time\b",
    r"\bnot\s+a\s+lot\s+of\s+time\b",
    r"\bno\s+time\s+to\s+(?:lose|waste|spare)\b",
    r"\bhave\s+no\s+time\b",
    r"\bshort\s+on\s+time\b",
    r"\blittle\s+time\b",
]

LIMITED_TIME_REGEX = re.compile("|".join(LIMITED_TIME_PATTERNS), re.IGNORECASE)

# Patterns that explicitly negate haste, rush, or speed
EXPLICIT_SPEED_NEGATION_PATTERNS = [
    re.compile(r"\b(?:don't|do\s+not|doesn't|does\s+not)\s+want\s+(?:a\s+)?(?:fast|quick|speedy)\s+(?:route|walk|path)\b", re.IGNORECASE),
    re.compile(r"\b(?:don't|do\s+not|doesn't|does\s+not)\s+want\s+to\s+rush\b", re.IGNORECASE),
    re.compile(r"\b(?:not|aren't|am\s+not|is\s+not|isn't)\s+(?:in\s+a\s+)?(?:rush|hurry)\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:rush|hurry)\b", re.IGNORECASE),
    re.compile(r"\bwithout\s+rushing\b", re.IGNORECASE),
    re.compile(r"\bavoid\s+rushing\b", re.IGNORECASE),
]

# Keyword sets for preference dimensions
ARCHITECTURE_KEYWORDS = [
    "architecture",
    "architectural",
    "art deco",
    "beaux-arts",
    "building",
    "buildings",
    "facade",
    "facades",
]

HISTORY_KEYWORDS = [
    "historic",
    "historical",
    "history",
    "heritage",
    "monument",
    "monumental",
]

SHORTEST_DISTANCE_KEYWORDS = [
    "shortest",
    "shortest walk",
    "shortest route",
    "least distance",
    "fewest blocks",
    "minimum distance",
    "min distance",
    "closest",
    "direct walk",
    "direct route",
]

MIN_TIME_KEYWORDS = [
    "quick",
    "quicker",
    "quickest",
    "fast",
    "faster",
    "fastest",
    "hurry",
    "hurrying",
    "rush",
    "rushing",
    "speedy",
    "rapid",
    "least time",
    "minimum time",
    "min time",
    "brief walk",
    "save time",
]

MORE_LANDMARKS_KEYWORDS = [
    "as many",
    "more landmarks",
    "many landmarks",
    "interesting landmarks",
    "interesting landmark",
    "landmarks",
    "many buildings",
    "interesting buildings",
    "interesting places",
    "interesting sights",
    "sightseeing",
    "sights",
    "attractions",
    "see everything",
    "explore",
    "points of interest",
    "scenic",
]

MIN_ONE_LANDMARK_PATTERNS = [
    re.compile(r'at least (?:one|1)\b', re.IGNORECASE),
    re.compile(r'with (?:at least one|a|an) (?:interesting )?landmark\b', re.IGNORECASE),
]

DETOUR_TOLERANCE_PATTERNS = [
    re.compile(
        r"\b(?:up to|no more than|at most|within)\s+(\d+(?:\.\d+)?)\s*%\s*(?:longer|extra|detour)\b",
        re.IGNORECASE,
    ),
]

MAX_DISTANCE_PATTERNS = [
    re.compile(
        r"\b(?:under|within|less than|at most|no more than|maximum|max)\s+"
        r"(\d+(?:\.\d+)?)\s*(km|kilometers?|miles?|mi|meters?|metres?|m)\b",
        re.IGNORECASE,
    ),
]

INTEREST_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "architecture": ("architecture", "architectural", "building", "buildings", "design"),
    "nature": ("nature", "natural", "greenery", "green space", "parks", "park"),
    "recreation": ("recreation", "relax", "relaxing"),
    "entertainment": ("entertainment", "bright lights", "shows", "theater", "theatre"),
    "urban_design": ("urban design", "city planning", "urban planning"),
    "religion": ("religion", "religious", "church", "cathedral", "faith"),
    "heritage": ("heritage", "historic", "historical", "history"),
    "art": ("art", "museum", "modern art", "moma"),
    "culture": ("culture", "cultural"),
}

GENEROUS_DETOUR_PHRASES = [
    "don't mind walking longer",
    "do not mind walking longer",
    "willing to walk longer",
    "happy to walk farther",
    "longer walk is fine",
    "worth a detour",
]

EXCLUDED_CATEGORY_KEYWORDS = [
    ("architecture", ARCHITECTURE_KEYWORDS),
    ("history", HISTORY_KEYWORDS),
    ("landmarks", MORE_LANDMARKS_KEYWORDS),
    ("nature", ["nature", "natural", "greenery", "green space", "park", "parks"]),
    ("recreation", ["recreation", "recreational"]),
    ("entertainment", ["entertainment", "theater", "theatre", "shows"]),
    ("religion", ["religion", "religious", "church", "cathedral", "faith"]),
    ("art", ["art", "museum", "moma"]),
    ("culture", ["culture", "cultural"]),
]

UNSUPPORTED_PREFERENCE_PATTERNS = [
    (re.compile(r"\b(?:greenery|green\s+spaces?|nature|natural\s+areas?)\b", re.IGNORECASE), "greenery"),
    (re.compile(r"\b(?:quiet(?:ness)?|peaceful|tranquil|low[- ]noise)\b", re.IGNORECASE), "quietness"),
]

# Patterns recognized as negative triggers
NEGATION_PATTERNS = [
    # Verb / verb-phrase negations
    r"\bdon't\s+want\b",
    r"\bdo\s+not\s+want\b",
    r"\bdoesn't\s+want\b",
    r"\bdoes\s+not\s+want\b",
    r"\bdidn't\s+want\b",
    r"\bdid\s+not\s+want\b",
    r"\bwon't\s+want\b",
    r"\bwouldn't\s+want\b",
    r"\bdon't\s+prefer\b",
    r"\bdo\s+not\s+prefer\b",
    r"\bdoesn't\s+prefer\b",
    r"\bdoes\s+not\s+prefer\b",
    r"\bnot\s+interested\s+in\b",
    r"\bno\s+interest\s+in\b",
    r"\baren't\s+interested\s+in\b",
    r"\bisn't\s+interested\s+in\b",
    r"\bam\s+not\s+interested\s+in\b",
    r"\bavoid\b",
    r"\bavoiding\b",
    r"\bavoids\b",
    r"\bwithout\b",
    r"\bdon't\s+care\s+(?:for|about)\b",
    r"\bdo\s+not\s+care\s+(?:for|about)\b",
    r"\bdoesn't\s+care\s+(?:for|about)\b",
    r"\bdoes\s+not\s+care\s+(?:for|about)\b",
    r"\bdon't\s+need\b",
    r"\bdo\s+not\s+need\b",
    r"\bdoesn't\s+need\b",
    r"\bdoes\s+not\s+need\b",
    r"\bno\s+need\s+for\b",
    r"\bnot\s+looking\s+for\b",
    r"\bskip\b",
    r"\bskipping\b",
    r"\bskips\b",
    r"\binstead\s+of\b",
    r"\brather\s+than\b",
    r"\bdislike\b",
    r"\bhate\b",
    r"\bnever\b",
    r"\bno\b",
    r"\bnot\b",
    r"\bdon't\s+have\b",
    r"\bdo\s+not\s+have\b",
    r"\bdoesn't\s+have\b",
    r"\bdoes\s+not\s+have\b",
    r"\bdon't\b",
    r"\bdo\s+not\b",
    r"\bdoesn't\b",
    r"\bdoes\s+not\b",
    r"\bdidn't\b",
    r"\bdid\s+not\b",
    r"\bwon't\b",
    r"\bwouldn't\b",
]

NEG_REGEX = re.compile("|".join(NEGATION_PATTERNS), re.IGNORECASE)

# Positive assertion / intent patterns that reset or override earlier negations in subsequent clauses
POSITIVE_INTENT_PATTERNS = [
    r"\bi\s+want\b",
    r"\bwe\s+want\b",
    r"\bi'd\s+like\b",
    r"\bi\s+would\s+like\b",
    r"\bwant\b",
    r"\bprefer\b",
    r"\bpreferred\b",
    r"\bgive\s+me\b",
    r"\bshow\s+me\b",
    r"\btake\s+the\b",
    r"\btake\s+a\b",
    r"\bchoose\b",
    r"\bfocus\s+on\b",
    r"\blooking\s+for\b",
    r"\binterested\s+in\b",
    r"\blove\b",
    r"\blike\b",
]

POS_REGEX = re.compile("|".join(POSITIVE_INTENT_PATTERNS), re.IGNORECASE)

# Boundary patterns that delineate independent clauses
BOUNDARY_PATTERN = re.compile(
    r'(?:[.;!?\n]|'
    r'\b(?:but|however|yet|although|though|except|whereas)\b|'
    r',\s*(?!(?:and|or)\b)'
    r')',
    re.IGNORECASE
)


def is_occurrence_negated(text: str, start: int, end: int) -> bool:
    """Determine if a keyword or pattern occurrence at text[start:end] is negated."""
    pre_text_all = text[:start]
    boundaries = list(BOUNDARY_PATTERN.finditer(pre_text_all))
    clause_start = boundaries[-1].end() if boundaries else 0

    clause_text = text[clause_start:start]

    # Find spans of positive limited-time expressions in clause_text
    limited_spans = [(m.start(), m.end()) for m in LIMITED_TIME_REGEX.finditer(clause_text)]

    # Find negative triggers in clause_text, ignoring any that fall inside a legitimate limited-time phrase
    raw_neg_matches = list(NEG_REGEX.finditer(clause_text))
    neg_matches = []
    for nm in raw_neg_matches:
        inside_limited = False
        for l_start, l_end in limited_spans:
            if l_start <= nm.start() and nm.end() <= l_end:
                inside_limited = True
                break
        if not inside_limited:
            neg_matches.append(nm)

    if not neg_matches:
        return False

    pos_matches = list(POS_REGEX.finditer(clause_text))

    # Filter out positive matches that overlap with negative triggers (e.g. 'want' inside "don't want")
    filtered_pos = []
    for pm in pos_matches:
        overlaps = False
        for nm in neg_matches:
            if not (pm.end() <= nm.start() or pm.start() >= nm.end()):
                overlaps = True
                break
        if not overlaps:
            filtered_pos.append(pm)

    last_neg_pos = neg_matches[-1].start()
    last_pos_pos = filtered_pos[-1].start() if filtered_pos else -1

    return last_neg_pos > last_pos_pos


def find_positive_match(text: str, keywords_or_patterns: list[str | re.Pattern]) -> bool:
    """Check if any keyword or pattern has at least one positive (non-negated) match."""
    for item in keywords_or_patterns:
        if isinstance(item, str):
            pattern = re.compile(r'\b' + re.escape(item) + r'\b', re.IGNORECASE)
        else:
            pattern = item

        for match in pattern.finditer(text):
            if not is_occurrence_negated(text, match.start(), match.end()):
                return True
    return False


def find_negated_match(text: str, keywords: list[str]) -> bool:
    """Check whether a supported preference keyword is explicitly negated."""
    for keyword in keywords:
        pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
        if any(is_occurrence_negated(text, match.start(), match.end()) for match in pattern.finditer(text)):
            return True
    return False


def extract_requested_landmark_ids(
    query: str | None,
    available_landmarks: list[dict[str, Any]] | None,
) -> list[str]:
    """Return exact catalog-name mentions in their spoken order."""
    if not query or not available_landmarks:
        return []

    mentions: list[tuple[int, int, str]] = []
    for landmark in available_landmarks:
        landmark_id = landmark.get("id")
        if not isinstance(landmark_id, str):
            continue
        alternate_names = landmark.get("alternate_names", [])
        if not isinstance(alternate_names, list):
            alternate_names = []
        names = [
            landmark.get("name"),
            landmark.get("prototype_display_name"),
            *alternate_names,
        ]
        for name in names:
            if not isinstance(name, str) or not name.strip():
                continue
            pattern = re.compile(
                r"(?<!\w)" + re.escape(name.strip()) + r"(?!\w)",
                re.IGNORECASE,
            )
            for match in pattern.finditer(query):
                mentions.append((match.start(), -(match.end() - match.start()), landmark_id))

    ordered: list[str] = []
    occupied: list[tuple[int, int]] = []
    for start, negative_length, landmark_id in sorted(mentions):
        end = start - negative_length
        if any(start < old_end and end > old_start for old_start, old_end in occupied):
            continue
        if landmark_id not in ordered:
            ordered.append(landmark_id)
        occupied.append((start, end))
    return ordered


def parse_preferences(
    query: str | None,
    available_landmarks: list[dict[str, Any]] | None = None,
) -> UserPreferences:
    """Parse a free-text user request into a structured UserPreferences object."""
    if not query or not isinstance(query, str) or not query.strip():
        # Missing or empty query: return neutral preferences
        return UserPreferences(raw_query=str(query or ""))

    raw_query = query.strip()
    text = raw_query.lower()

    prefs = UserPreferences(raw_query=raw_query)
    prefs.requested_landmark_ids = extract_requested_landmark_ids(
        raw_query, available_landmarks
    )
    prefs.unsupported_preferences = [
        label
        for pattern, label in UNSUPPORTED_PREFERENCE_PATTERNS
        if pattern.search(text)
    ]

    # 1a. Check for explicit hard numeric time limit
    for pattern in HARD_TIME_LIMIT_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                mins = float(match.group(1))
                if mins > 0:
                    prefs.max_duration_minutes = mins
                    prefs.minimum_walking_time = max(prefs.minimum_walking_time, 0.7)
                    break
            except ValueError:
                pass

    # 1b. Check for available walking-time budget
    for pattern in TIME_BUDGET_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                mins = float(match.group(1))
                if mins > 0:
                    prefs.walking_time_budget_minutes = mins
                    break
            except ValueError:
                pass

    for pattern in MAX_DISTANCE_PATTERNS:
        match = pattern.search(raw_query)
        if not match:
            continue
        amount = float(match.group(1))
        unit = match.group(2).lower()
        multiplier = (
            1609.344 if unit in {"mile", "miles", "mi"}
            else 1000.0 if unit in {"km", "kilometer", "kilometers"}
            else 1.0
        )
        prefs.max_distance_m = amount * multiplier
        break

    # 2. Check for qualitative time constraints ("don't have much time", etc.)
    for phrase in QUALITATIVE_TIME_PHRASES:
        pattern = re.compile(r'\b' + re.escape(phrase) + r'\b', re.IGNORECASE)
        match_found = False
        for m in pattern.finditer(text):
            if not is_occurrence_negated(text, m.start(), m.end()):
                prefs.is_qualitative_time_constrained = True
                prefs.minimum_walking_time = max(prefs.minimum_walking_time, 0.8)
                # If no explicit numeric minutes were provided, apply a default 10-minute constraint
                if prefs.max_duration_minutes is None:
                    prefs.max_duration_minutes = 10.0
                match_found = True
                break
        if match_found:
            break

    # 3. Check for shortest distance preference
    if find_positive_match(text, SHORTEST_DISTANCE_KEYWORDS):
        prefs.shortest_distance = 1.0

    # 4. Check for minimum walking time preference
    if find_positive_match(text, MIN_TIME_KEYWORDS):
        prefs.minimum_walking_time = max(prefs.minimum_walking_time, 1.0)

    # 4b. Explicit negation of speed / rush overrides qualitative minimum walking time
    if any(p.search(text) for p in EXPLICIT_SPEED_NEGATION_PATTERNS):
        prefs.minimum_walking_time = 0.0

    # 5. Check for architecture & historic preference
    if find_positive_match(text, ARCHITECTURE_KEYWORDS):
        prefs.architecture = max(prefs.architecture, 1.0)

    if find_positive_match(text, HISTORY_KEYWORDS):
        prefs.history = 1.0

    # 6. Check for more landmarks preference
    if find_positive_match(text, MORE_LANDMARKS_KEYWORDS):
        prefs.more_landmarks = max(prefs.more_landmarks, 1.0)

    # 7. Check for minimum landmark requirements (e.g., "at least one landmark")
    if find_positive_match(text, MIN_ONE_LANDMARK_PATTERNS):
        prefs.min_landmarks = 1
        prefs.more_landmarks = max(prefs.more_landmarks, 0.6)

    prefs.excluded_categories = [
        category
        for category, keywords in EXCLUDED_CATEGORY_KEYWORDS
        if find_negated_match(text, keywords)
    ]

    for pattern in DETOUR_TOLERANCE_PATTERNS:
        match = pattern.search(raw_query)
        if match:
            percentage = float(match.group(1))
            prefs.max_detour_ratio = min(2.0, max(1.0, 1.0 + percentage / 100.0))
            break
    if prefs.max_detour_ratio is None and any(
        phrase in text for phrase in GENEROUS_DETOUR_PHRASES
    ):
        prefs.max_detour_ratio = 2.0

    available_categories = {
        category
        for landmark in (available_landmarks or [])
        for category in landmark.get("categories", [])
        if isinstance(category, str)
    }
    available_categories.update({"history", "landmarks"})
    category_mentions = []
    for category, keywords in INTEREST_CATEGORY_KEYWORDS.items():
        if category not in available_categories:
            continue
        positions = [
            match.start()
            for keyword in keywords
            for match in re.finditer(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", text)
            if not is_occurrence_negated(text, match.start(), match.end())
        ]
        if positions:
            category_mentions.append((min(positions), category))
    category_mentions.sort()
    prefs.preferred_categories = [category for _, category in category_mentions]
    if category_mentions:
        prefs.primary_interest = category_mentions[0][1]
        prefs.secondary_interests = [
            category for _, category in category_mentions[1:]
        ]

    if prefs.shortest_distance > 0 or prefs.minimum_walking_time > 0:
        prefs.route_priority = "efficiency"
    if (
        prefs.more_landmarks > 0
        or prefs.max_detour_ratio is not None
        or any(phrase in text for phrase in GENEROUS_DETOUR_PHRASES)
    ):
        prefs.route_priority = (
            "balanced"
            if prefs.route_priority == "efficiency"
            else "sightseeing"
        )

    route_count_match = re.search(
        r"\b(?:three|3)\s+(?:different\s+)?routes?\b",
        raw_query,
        re.IGNORECASE,
    )
    if not route_count_match:
        route_count_match = re.search(
            r"\b(?:four|4)\s+(?:different\s+)?routes?\b",
            raw_query,
            re.IGNORECASE,
        )
    if route_count_match:
        word_or_digit = route_count_match.group(0).lower()
        prefs.requested_route_count = 4 if "4" in word_or_digit or "four" in word_or_digit else 3

    if prefs.requested_landmark_ids:
        prefs.sightseeing_waypoint_ids = list(prefs.requested_landmark_ids)
    else:
        ranked_landmarks: list[str] = []
        preferred_categories = set(prefs.preferred_categories)
        for landmark in available_landmarks or []:
            landmark_id = landmark.get("id")
            categories = set(landmark.get("categories", []))
            if not isinstance(landmark_id, str):
                continue
            if (
                (prefs.architecture > 0 and {"architecture", "art_deco"} & categories)
                or (prefs.history > 0 and {"historic_landmark", "history"} & categories)
                or preferred_categories & categories
            ):
                ranked_landmarks.append(landmark_id)
        prefs.sightseeing_waypoint_ids = ranked_landmarks[:2]
        prefs.preferred_landmark_ids = ranked_landmarks[:2]

    return prefs

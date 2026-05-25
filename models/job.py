"""Pydantic models for the jobs pipeline.

MongoDB layout (SPEC 01):
  - company / location stored as nested BSON dicts
  - salary / classification / quality flattened to top level
  - double-write: url+link, posted_at+published_at (1-release compat window)
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from bson import ObjectId
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Language(str, Enum):
    EN = "en"
    IT = "it"
    ES = "es"
    DE = "de"
    FR = "fr"
    PT = "pt"
    OTHER = "other"


class JobStatus(str, Enum):
    """Lifecycle status — public listing vocabulary (SDD §I.4).

    Old `VALID`/`PREMIUM` values were collapsed into `ACTIVE`. The premium/valid
    distinction now lives on `JobQuality.quality_tier` (see QualityTier).
    Migration: scripts/migrate_status_vocab.py rewrites legacy rows.
    """

    ACTIVE = "active"
    EXPIRED = "expired"
    CLOSED = "closed"
    DRAFT = "draft"
    REJECTED_QUALITY = "rejected_quality"
    REJECTED_PREFILTER = "rejected_prefilter"


class QualityTier(str, Enum):
    """Quality tier sub-classification for ACTIVE jobs (SDD §D.1)."""

    VALID = "valid"
    PREMIUM = "premium"


class RemoteMode(str, Enum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    FREELANCE = "freelance"
    INTERNSHIP = "internship"
    UNKNOWN = "unknown"


class RoleFamily(str, Enum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    FULLSTACK = "fullstack"
    DEVOPS = "devops"
    DATA = "data"
    ML = "ml"
    MOBILE = "mobile"
    QA = "qa"
    SECURITY = "security"
    DESIGN = "design"
    PM = "pm"
    OTHER = "other"


class Seniority(str, Enum):
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    PRINCIPAL = "principal"
    UNKNOWN = "unknown"


class Category(str, Enum):
    SOFTWARE_ENGINEERING = "software-engineering"
    DEVOPS_SYSADMIN = "devops-sysadmin"
    DATA_ML = "data-ml"
    DESIGN = "design"
    PRODUCT_MANAGEMENT = "product-management"
    ENGINEERING_MANAGEMENT = "engineering-management"
    SECURITY = "security"
    QA_TESTING = "qa-testing"
    MOBILE = "mobile"
    OTHER_IT = "other-it"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class GeoPoint(BaseModel):
    """GeoJSON Point for MongoDB 2dsphere index."""

    type: str = "Point"
    coordinates: list[float]  # [longitude, latitude]

    @field_validator("coordinates")
    @classmethod
    def validate_coords(cls, v: list[float]) -> list[float]:
        if len(v) != 2:
            msg = "coordinates must be [longitude, latitude]"
            raise ValueError(msg)
        lng, lat = v
        if not (-180 <= lng <= 180) or not (-90 <= lat <= 90):
            msg = "coordinates out of range"
            raise ValueError(msg)
        return v


class JobSource(BaseModel):
    """Scraper origin metadata."""

    source: str
    external_id: str | None = None


class JobContent(BaseModel):
    """Textual content of the job offer."""

    title: str
    title_normalized: str
    description: str
    description_md: str | None = None
    language: Language


class JobCompany(BaseModel):
    """Company sub-document (stored nested in MongoDB)."""

    name: str
    name_normalized: str
    logo: str | None = None
    id: str | None = None  # serialized ObjectId FK to companies._id


class JobLocation(BaseModel):
    """Location sub-document (stored nested in MongoDB)."""

    raw: str | None = None
    formatted_address: str | None = None
    city: str | None = None
    country: str | None = None  # ISO-3166 alpha-2
    geo: GeoPoint | None = None


class JobSalary(BaseModel):
    """Salary range (flattened to top-level in MongoDB)."""

    min: int | None = None
    max: int | None = None
    currency: str | None = None  # ISO-4217
    period: str | None = None  # annual | monthly | hourly


class JobClassification(BaseModel):
    """AI-extracted classification (flattened to top-level in MongoDB).

    Includes salary because the AI classifier extracts salary along with
    the rest. JobSalary on Job is populated from these fields by the caller.

    technical_skills vs skills split is done by the skills lexicon (claude-06).
    Until then classify_job() puts all Groq skills into technical_skills.
    """

    technical_skills: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    category: Category | None = None
    role_family: RoleFamily = RoleFamily.OTHER
    seniority: Seniority = Seniority.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    remote_mode: RemoteMode = RemoteMode.UNKNOWN
    remote: bool = False
    languages_required: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    # Salary extracted by AI (copied to Job.salary by the pipeline stage)
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    ai_confidence: float = 0.0
    ai_model: str = ""
    ai_call_at: datetime | None = None

    @model_validator(mode="after")
    def sync_remote_flag(self) -> "JobClassification":
        self.remote = self.remote_mode in (RemoteMode.HYBRID, RemoteMode.REMOTE)
        return self


class JobQuality(BaseModel):
    """Quality gate output (flattened to top-level in MongoDB).

    quality_tier: VALID|PREMIUM only when status == ACTIVE (SDD §D.1).
    geocode_pending: True when raw location present but coordinates missing —
        the soft-path keeps the job ACTIVE and a separate `geocode` CLI run
        backfills coordinates later (SDD §A.5 soft-path / §A.8).
    """

    quality_score: int = 0  # 0-100
    quality_tier: QualityTier | None = None
    geocode_pending: bool = False


# ---------------------------------------------------------------------------
# Main Job model
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


class Job(BaseModel):
    """Canonical job document matching SPEC 01 schema."""

    id: str | None = None  # MongoDB _id serialized as string

    # Identity
    url: str
    dedup_hash: str
    cross_source_hash: str = ""

    # Sub-models
    source_info: JobSource
    content: JobContent
    company: JobCompany
    location: JobLocation = Field(default_factory=JobLocation)
    salary: JobSalary = Field(default_factory=JobSalary)
    classification: JobClassification = Field(default_factory=JobClassification)
    quality: JobQuality = Field(default_factory=JobQuality)

    # AI enrichment flag
    enriched_by_ai: bool = False

    # Lifecycle
    status: JobStatus = JobStatus.ACTIVE
    reject_reason: str | None = None
    posted_at: datetime
    first_seen_at: datetime = Field(default_factory=_now_utc)
    last_seen_at: datetime = Field(default_factory=_now_utc)
    expires_at: datetime | None = None
    last_probed_at: datetime | None = None

    # Legacy FK (keep until Bun API drops seniority_id reads)
    seniority_id: str | None = None

    # Bun-managed counters (scraper writes 0 on insert, never updates)
    views_count: int = 0
    clicks_count: int = 0
    likes_count: int = 0
    dislikes_count: int = 0
    comments_count: int = 0

    created_at: datetime = Field(default_factory=_now_utc)
    updated_at: datetime = Field(default_factory=_now_utc)

    def to_mongo_doc(self) -> dict[str, Any]:
        """Produce a flat MongoDB document matching SPEC 01.

        Flattens salary / classification / quality to top level.
        Keeps company / location as nested dicts.
        Double-writes link+url, published_at+posted_at for Bun compat.
        """
        cl = self.classification
        doc: dict[str, Any] = {
            # Identity
            "url": self.url,
            "link": self.url,  # Bun compat — remove after 1 release
            "source": self.source_info.source,
            "external_id": self.source_info.external_id,
            "dedup_hash": self.dedup_hash,
            "cross_source_hash": self.cross_source_hash,
            # Prisma FK — written as ObjectId so Prisma @db.ObjectId reads it correctly
            "company_id": ObjectId(self.company.id) if self.company.id else None,
            # Content
            "title": self.content.title,
            "title_normalized": self.content.title_normalized,
            "description": self.content.description,
            "description_md": self.content.description_md,
            "language": self.content.language.value,
            # Dates
            "posted_at": self.posted_at,
            "published_at": self.posted_at,  # Bun compat — remove after 1 release
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "expires_at": self.expires_at,
            "last_probed_at": self.last_probed_at,
            # Status
            "status": self.status.value,
            "reject_reason": self.reject_reason,
            "enriched_by_ai": self.enriched_by_ai,
            # Company (nested)
            "company": {
                "name": self.company.name,
                "name_normalized": self.company.name_normalized,
                "logo": self.company.logo,
                "id": self.company.id,
            },
            # Location — Prisma schema has `location String?`, write raw string
            "location": self.location.raw,
            # Flat location fields for scraper indexing (outside Prisma schema)
            "location_raw": self.location.raw,
            "city": self.location.city,
            "country": self.location.country,
            "formatted_address": self.location.formatted_address,
            "location_geo": (
                {
                    "type": self.location.geo.type,
                    "coordinates": self.location.geo.coordinates,
                }
                if self.location.geo
                else None
            ),
            # Classification (flattened)
            "remote": cl.remote,
            "remote_mode": cl.remote_mode.value,
            "employment_type": cl.employment_type.value,
            "technical_skills": cl.technical_skills,
            "skills": cl.skills,
            "category": cl.category.value if cl.category is not None else None,
            "role_family": cl.role_family.value,
            "seniority": cl.seniority.value,
            "languages_required": cl.languages_required,
            "requirements": cl.requirements,
            "benefits": cl.benefits,
            "quality_flags": cl.quality_flags,
            "ai_confidence": cl.ai_confidence,
            "ai_model": cl.ai_model,
            "ai_call_at": cl.ai_call_at,
            # Salary (flattened — prefer explicit JobSalary, fall back to classification)
            "salary_min": self.salary.min if self.salary.min is not None else cl.salary_min,
            "salary_max": self.salary.max if self.salary.max is not None else cl.salary_max,
            "currency": self.salary.currency or cl.currency,
            "salary_period": self.salary.period,
            # Quality (flattened — nested mirror kept for backend Prisma reads)
            "quality_score": self.quality.quality_score,
            "quality_tier": (
                self.quality.quality_tier.value if self.quality.quality_tier is not None else None
            ),
            "quality": {
                "quality_score": self.quality.quality_score,
                "quality_tier": (
                    self.quality.quality_tier.value
                    if self.quality.quality_tier is not None
                    else None
                ),
                "geocode_pending": self.quality.geocode_pending,
            },
            # Legacy
            "seniority_id": self.seniority_id,
            # Counters
            "views_count": self.views_count,
            "clicks_count": self.clicks_count,
            "likes_count": self.likes_count,
            "dislikes_count": self.dislikes_count,
            "comments_count": self.comments_count,
            # Timestamps
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.id is not None:
            doc["_id"] = self.id
        return doc

    @classmethod
    def from_mongo_doc(cls, doc: dict[str, Any]) -> "Job":
        """Reconstruct a Job from a MongoDB document."""
        company_raw = doc.get("company") or {}
        # Handle both old nested format and new flat format (Prisma compat)
        location_field = doc.get("location")
        if isinstance(location_field, dict):
            # Legacy nested format
            location_str = location_field.get("raw")
            city_val = location_field.get("city")
            country_val = location_field.get("country")
            formatted_val = location_field.get("formatted_address")
            geo_raw = location_field.get("geo")
        else:
            # New flat format
            location_str = location_field
            city_val = doc.get("city")
            country_val = doc.get("country")
            formatted_val = doc.get("formatted_address")
            geo_raw = doc.get("location_geo")

        geo: GeoPoint | None = None
        if geo_raw and geo_raw.get("coordinates"):
            geo = GeoPoint(
                type=geo_raw.get("type", "Point"),
                coordinates=geo_raw["coordinates"],
            )

        language_val = doc.get("language", "other")
        try:
            language = Language(language_val)
        except ValueError:
            language = Language.OTHER

        return cls(
            id=str(doc["_id"]) if "_id" in doc else None,
            url=doc.get("url") or doc.get("link", ""),
            dedup_hash=doc.get("dedup_hash", ""),
            cross_source_hash=doc.get("cross_source_hash", ""),
            source_info=JobSource(
                source=doc.get("source", ""),
                external_id=doc.get("external_id"),
            ),
            content=JobContent(
                title=doc.get("title", ""),
                title_normalized=doc.get("title_normalized", ""),
                description=doc.get("description", ""),
                description_md=doc.get("description_md"),
                language=language,
            ),
            company=JobCompany(
                name=company_raw.get("name", ""),
                name_normalized=company_raw.get("name_normalized", ""),
                logo=company_raw.get("logo"),
                id=str(company_raw["id"]) if company_raw.get("id") else None,
            ),
            location=JobLocation(
                raw=location_str,
                formatted_address=formatted_val,
                city=city_val,
                country=country_val,
                geo=geo,
            ),
            salary=JobSalary(
                min=doc.get("salary_min"),
                max=doc.get("salary_max"),
                currency=doc.get("currency"),
                period=doc.get("salary_period"),
            ),
            classification=JobClassification(
                technical_skills=doc.get("technical_skills", []),
                skills=doc.get("skills", []),
                category=_safe_enum(Category, doc.get("category"), None),
                role_family=_safe_enum(RoleFamily, doc.get("role_family"), RoleFamily.OTHER),
                seniority=_safe_enum(Seniority, doc.get("seniority"), Seniority.UNKNOWN),
                employment_type=_safe_enum(
                    EmploymentType, doc.get("employment_type"), EmploymentType.UNKNOWN
                ),
                remote_mode=_safe_enum(RemoteMode, doc.get("remote_mode"), RemoteMode.UNKNOWN),
                languages_required=doc.get("languages_required", []),
                requirements=doc.get("requirements", []),
                benefits=doc.get("benefits", []),
                quality_flags=doc.get("quality_flags", []),
                ai_confidence=doc.get("ai_confidence", 0.0),
                ai_model=doc.get("ai_model", ""),
                ai_call_at=doc.get("ai_call_at"),
            ),
            quality=_quality_from_doc(doc),
            enriched_by_ai=bool(doc.get("enriched_by_ai", False)),
            status=_safe_enum(JobStatus, doc.get("status"), JobStatus.ACTIVE),
            reject_reason=doc.get("reject_reason"),
            posted_at=doc.get("posted_at") or doc.get("published_at") or _now_utc(),
            first_seen_at=doc.get("first_seen_at", _now_utc()),
            last_seen_at=doc.get("last_seen_at", _now_utc()),
            expires_at=doc.get("expires_at"),
            last_probed_at=doc.get("last_probed_at"),
            seniority_id=str(doc["seniority_id"]) if doc.get("seniority_id") else None,
            views_count=doc.get("views_count", 0),
            clicks_count=doc.get("clicks_count", 0),
            likes_count=doc.get("likes_count", 0),
            dislikes_count=doc.get("dislikes_count", 0),
            comments_count=doc.get("comments_count", 0),
            created_at=doc.get("created_at", _now_utc()),
            updated_at=doc.get("updated_at", _now_utc()),
        )


# ---------------------------------------------------------------------------
# RawJob — output of the Normalize stage, input to Pre-filter
# ---------------------------------------------------------------------------


class RawJob(BaseModel):
    """Normalized scraper output before AI classification."""

    url: str
    title: str
    description: str
    company_name: str
    source: str
    posted_at: datetime | None = None
    location_raw: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    original_language: str | None = None
    external_id: str | None = None

    @field_validator("salary_min", "salary_max", mode="before")
    @classmethod
    def coerce_salary_to_int(cls, v: object) -> int | None:
        if v is None:
            return None
        try:
            return int(float(str(v)))
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_str.lower().split())


def compute_dedup_hash(title: str, company_name: str, source: str) -> str:
    """sha1(title_normalized|company_normalized|source_lower) per SPEC 01 §3."""
    normalized = f"{normalize_text(title)}|{normalize_text(company_name)}|{source.lower()}"
    return hashlib.sha1(normalized.encode()).hexdigest()  # noqa: S324


def compute_cross_source_hash(title: str, company_name: str) -> str:
    """sha1(title_normalized|company_normalized) — stable across sources."""
    normalized = f"{normalize_text(title)}|{normalize_text(company_name)}"
    return hashlib.sha1(normalized.encode()).hexdigest()  # noqa: S324


def _safe_enum(enum_cls: type, value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        return default


def _quality_from_doc(doc: dict[str, Any]) -> JobQuality:
    """Reconstruct JobQuality preferring nested `quality.*` over flat fields."""
    nested = doc.get("quality") if isinstance(doc.get("quality"), dict) else {}
    score = nested.get("quality_score", doc.get("quality_score", 0)) or 0
    tier_raw = nested.get("quality_tier", doc.get("quality_tier"))
    tier = _safe_enum(QualityTier, tier_raw, None) if tier_raw else None
    geocode_pending = bool(nested.get("geocode_pending", doc.get("geocode_pending", False)))
    return JobQuality(
        quality_score=int(score),
        quality_tier=tier,
        geocode_pending=geocode_pending,
    )

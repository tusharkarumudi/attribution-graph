"""attribution-graph: evidence-strength scoring for entity attribution.

Public API::

    from attribution_graph import (
        AttributionGraph, Claim, Identifier, IdKind, Predicate, Reliability,
        Entity, EntityType, CaseScope, SourceClass, Engine, assess, resolve,
    )

The library performs no network I/O. Supply collectors satisfying the
``Collector`` protocol and, ideally, a corpus-backed ``SelectivityIndex``.
"""

from ._version import __version__  # noqa: F401
from .calibration import (
    BandPerformance,
    BrierDecomposition,
    IsotonicScaler,
    LabelledPair,
    PlattScaler,
    band_performance,
    brier,
    calibration_report,
    correct_for_prevalence,
    expected_calibration_error,
    load_corpus,
    reliability_diagram,
    sample_prevalence,
    save_corpus,
    split_by_case,
)
from .engine import PERSON_SCOPED_COLLECTORS, Engine
from .evidence import (
    Capture,
    EvidenceLog,
    declaration_template,
    sha256_bytes,
    write_evidence_package,
)
from .export import (
    CONFIDENCE_DISCLAIMER,
    SOURCE_TERMS,
    report,
    source_attribution,
    to_cypher,
    to_ftm,
    to_html,
    write_all,
)
from .fetchpolicy import FetchDecision, PolicyEngine, RobotsPolicy
from .filters import Verdict, evaluate, load_blacklist
from .minimise import canonical_forms
from .minimise import scrub as scrub_identifiers
from .model import (
    AttributionGraph,
    Claim,
    Entity,
    EntityType,
    Identifier,
    IdKind,
    Predicate,
    Reliability,
)
from .negative import (
    COVERAGE,
    AbsenceKind,
    Expectation,
    SourceCoverage,
    absence_claim,
    absence_llr,
    common_control_expectations,
    expectation_claims,
    render_expectations,
)
from .obfuscation import (
    DELIBERATE,
    NormalizationResult,
    Obfuscation,
    ObfuscationLog,
    canonical,
    normalize_value,
    scan_text,
)
from .protocol import Collector, CompositeIndex, InMemoryIndex, SelectivityIndex
from .resolve import ResolutionResult, resolve
from .scope import DENIED_SOURCE_CLASSES, CaseScope, PolicyError, SourceClass
from .scoring import (
    DEFINITIONAL_COLLECTORS,
    DEFINITIONAL_SOURCE_CLASSES,
    ESTIMATIVE,
    Assessment,
    Band,
    assess,
    claim_llr,
    is_definitional,
    selectivity,
)
from .screenshot import (
    ElementLocation,
    Screenshot,
    ScreenshotCapturer,
    ShotStatus,
    available_renderer,
)
from .trail import Source, Step, StepKind, Trail
from .translit import (
    LOOKUP_LIMIT,
    NameMatch,
    Variant,
    lookup_variants,
    rule_catalog,
    variant_provenance,
    variants,
)
from .translit import match as name_match
from .verify import VerificationResult, verify_package

__all__ = [
    "canonical_forms",
    "scrub_identifiers",
    "verify_package",
    "VerificationResult",
    "is_definitional",
    "DEFINITIONAL_COLLECTORS",
    "DEFINITIONAL_SOURCE_CLASSES",
    "AttributionGraph", "Claim", "Entity", "EntityType", "IdKind", "Identifier",
    "Predicate", "Reliability", "Collector", "SelectivityIndex", "InMemoryIndex",
    "CompositeIndex", "CaseScope", "PolicyError", "SourceClass",
    "DENIED_SOURCE_CLASSES", "Assessment", "Band", "ESTIMATIVE", "assess",
    "claim_llr", "selectivity", "ResolutionResult", "resolve", "Engine",
    "PERSON_SCOPED_COLLECTORS", "Verdict", "evaluate", "load_blacklist",
    "report", "to_cypher", "to_ftm", "to_html", "write_all",
    "source_attribution", "SOURCE_TERMS", "CONFIDENCE_DISCLAIMER",
    "Trail", "Step", "StepKind", "Source",
    "name_match", "variants", "rule_catalog", "NameMatch", "Variant",
    "lookup_variants", "variant_provenance", "LOOKUP_LIMIT",
    "AbsenceKind", "SourceCoverage", "COVERAGE", "absence_claim", "absence_llr",
    "Expectation", "common_control_expectations", "expectation_claims",
    "render_expectations",
    "EvidenceLog", "Capture", "declaration_template", "write_evidence_package",
    "sha256_bytes", "RobotsPolicy", "PolicyEngine", "FetchDecision",
    "Obfuscation", "ObfuscationLog", "NormalizationResult", "DELIBERATE",
    "ScreenshotCapturer", "Screenshot", "ElementLocation", "ShotStatus",
    "available_renderer",
    "normalize_value", "canonical", "scan_text",
    "LabelledPair", "load_corpus", "save_corpus", "split_by_case",
    "reliability_diagram", "expected_calibration_error", "brier",
    "BrierDecomposition", "band_performance", "BandPerformance",
    "PlattScaler", "IsotonicScaler", "calibration_report",
    "correct_for_prevalence", "sample_prevalence", "__version__",
]

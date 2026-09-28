"""engine/config.py — engine-wide tuning constants.

All constants are defined here as the single source of truth.
Import from here instead of redefining in individual modules.
"""

# Evidence weights used in the noisy-OR combination.
# MO wording similarity never creates a link on its own (Rule 3).
W = {
    "phone":            0.90,
    "upi":              0.90,
    "vehicle_reg":      0.85,
    "person_strong":    0.70,
    "vehicle_last4_desc": 0.55,
    "person_name":      0.30,
    "vehicle_last4":    0.25,
    "mo_support":       0.30,
}

# Link score thresholds
STRONG = 0.80   # score >= STRONG → confirmed link
REVIEW = 0.50   # score >= REVIEW → sent for review

# MO similarity thresholds
ATTACH_MO_MIN = 0.30   # min MO similarity to suggest attaching an unlinked FIR to a cluster

# Hub detection: an identifier seen across >= HUB_MIN_TYPES distinct crime types
# is treated as a hub and its link weight is down-weighted by 0.3×.
HUB_MIN_TYPES = 3

# Version tag written to every result row produced by this engine (Rule 5).
ENGINE_VERSION = "engine_v1"

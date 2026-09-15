"""Three-layer time model, gap analysis and cross-device correlation (doc 3 §8; FR-50..FR-62).

Only this package constructs `ReferenceTime` (models.py contract). Plugins hand over
`DeviceTime` exactly as stored; everything downstream — report, UI, correlation — takes
normalised times from here or displays device-local time under an explicit
"absolute time not established" banner (FR-53).
"""

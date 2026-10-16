"""Lodgings lane passport: move one lane between machines at a checkpoint.

A passport is a sealed directory: one git bundle of every lane, gate and
archive ref, plus text receipts (custody, pipeline, brief, status, decisions).
The origin is never written until the target confirms an import.
"""

VERSION = "0.1.0"
PASSPORT_SCHEMA = "lodgings.passport/1"
LANE_SCHEMA = "lodgings.lane/1"
IMPORT_SCHEMA = "lodgings.passport-import/1"
CONFIRM_SCHEMA = "lodgings.passport-confirm/1"

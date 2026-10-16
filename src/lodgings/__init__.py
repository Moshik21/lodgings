"""Lodgings: a pool of hosts where work can start on, or move to, any host that can honestly run it.

It holds the host registry, the capability probe and the toolchain fingerprint, plus host states, drain inputs,
goodbye beats and evacuation offers (advice only).
"""
NAME = "Lodgings"
VERSION = "0.3.0"

# Schema ids are versioned apart from the tool (docs/capabilities.md).
HOSTS_SCHEMA = "lodgings.hosts/1"
FACTS_SCHEMA = "lodgings.facts"
FACTS_VERSION = "1.0"
CAPS_SCHEMA = "lodgings.capabilities"
CAPS_VERSION = "1.0"
CHIPS_SCHEMA = "lodgings.chips"
CHIPS_VERSION = "1.0"
# lodgings.beat/1 and lodgings.event/1 live in records.py (docs/drain.md).

"""Journey runner package — YAML-driven AI journey execution for SimDrive.

Public surface:
  - schema.Journey / load_journey   — parse + validate journey YAML
  - persona.Persona / load_persona  — parse + validate persona YAML
  - runner.run_journey              — execute a journey against a session
  - ci.run_ci                       — discover + run all journeys, emit JUnit XML
"""
from __future__ import annotations

"""Direction: the model-free core of a movie's direction (ADR 0009, ADR 0010).

It decides, per cut, what the render must be given: the cut's coverage and the technique the policy recommends.
Everything here is pure. Integrations declare what they can lower (`capabilities`) and carry their own
model-specific knowledge (knowledge packs); nothing in this package imports an integration.
"""

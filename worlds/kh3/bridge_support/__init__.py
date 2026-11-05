"""Shared KH3 bridge services; import each service from its owning module.

Keep package initialization lightweight so paths can be imported by the IPC
transport before files.py binds its transport hooks.
"""

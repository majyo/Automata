"""Workspace layer: file and path capabilities.

The workspace layer owns path constraints, file reading and writing, search
and patch application. It returns plain file/process results and never a
tool result, which is what keeps ``workspace`` independent of ``tools``.
"""

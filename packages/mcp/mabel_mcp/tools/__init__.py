"""The nine handlers.

Grouped by what they are about rather than one file per tool, because the
interesting rules are shared: the two lookup tools both refuse to surface a job
value, the two scheduling tools both refuse to invent a time, and the three
writing tools all run inside one transaction with whatever else the call did.
"""

"""The tools this server advertises, one module per subject.

Importing a module here registers its tools with `registry.REGISTRY`,
which is a side effect and the reason `server.py` imports all of them
at once without naming anything from them. `registry` itself holds no
tool: it is the decorator, the argument readers and the reply shaping
that every module below uses.
"""

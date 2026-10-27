"""CP2 fixture: imports the package under an alias, then reads the moving symbol through an
attribute chain whose alias is bound only to the leading component (`import pkg as p` then
`p.module_a.foo`)."""
import pkg as p


def call_it():
    return p.module_a.foo()  # MARKER: importer-alias-attr

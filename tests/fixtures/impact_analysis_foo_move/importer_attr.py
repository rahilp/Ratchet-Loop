"""CP2 fixture: imports the module itself, then reads the moving symbol off it by attribute later."""
import pkg.module_a


def call_it():
    return pkg.module_a.foo()  # MARKER: importer-attr

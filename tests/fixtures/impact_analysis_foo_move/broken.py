"""CP2 fixture: a deliberate syntax error, so the scan must report this file as manual-check-needed
without aborting the rest of the run."""
def this_is_broken(
    return "missing closing paren above, and no colon on this def line either"

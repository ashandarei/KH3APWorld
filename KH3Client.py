import json
import os
import sys

# Importing any ``worlds.*`` module normally loads every installed world.  The
# KH3 client only needs KH3 (and the generic world that the loader adds
# automatically), so constrain the catalog before importing the client.
_WORLD_LOAD_PATHS_ENV = "AP_WORLD_LOAD_PATHS"
_previous_world_load_paths = os.environ.get(_WORLD_LOAD_PATHS_ENV)
os.environ[_WORLD_LOAD_PATHS_ENV] = json.dumps([
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "worlds", "kh3"),
])

try:
    import ModuleUpdate

    ModuleUpdate.update()

    import Utils
    from worlds.kh3.Client import launch
finally:
    if _previous_world_load_paths is None:
        os.environ.pop(_WORLD_LOAD_PATHS_ENV, None)
    else:
        os.environ[_WORLD_LOAD_PATHS_ENV] = _previous_world_load_paths

if __name__ == "__main__":
    Utils.init_logging("KH3Client", exception_logger="Client")
    launch(*sys.argv[1:])

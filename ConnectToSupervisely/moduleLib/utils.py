import functools
import json
import logging
import os
from importlib.metadata import distributions
from pathlib import Path

import slicer

RESTORE_LIB_FILE = os.path.join(Path.home(), "supervisely_slicer_installed_packages.json")

# The Supervisely SDK release this module installs and is tested against.
SUPERVISELY_VERSION = "6.74.45"

# ------------------------------------- Decorators ------------------------------------- #


def log_method_call(func):
    @functools.wraps(func)
    def wrapper(self):
        logging.debug(f"Called method: {func.__name__}")
        return func(self)

    return wrapper


def log_method_call_args(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        logging.debug(f"Called method: {func.__name__}")
        return func(self, *args, **kwargs)

    return wrapper


def timer_decorator(func):
    import time

    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        print(f"Function {func.__name__} took {end_time - start_time} seconds to run.")
        return result

    return wrapper


def get_installed_libraries_info():
    installed_packages = [(d.metadata["Name"], d.version) for d in distributions()]
    installed_packages_dict = dict(installed_packages)
    return installed_packages_dict


def backup_installed_libraries_info(before_installation, after_installation):
    updated_libraries = {
        lib: {"old_version": before_installation[lib], "new_version": after_installation[lib]}
        for lib in after_installation
        if lib in before_installation and before_installation[lib] != after_installation[lib]
    }

    with open(RESTORE_LIB_FILE, "w") as f:
        json.dump(
            {
                "before_installation": before_installation,
                "after_installation": after_installation,
                "updated_libraries": updated_libraries,
            },
            f,
        )


def restore_libraries(button):
    from moduleLib import SuperviselyDialog

    with open(RESTORE_LIB_FILE, "r") as f:
        backup_info = json.load(f)
    updated_libraries = backup_info.get("updated_libraries", {})
    libraries_list = "\n".join(
        [
            f"- [{lib}] Previous version: {updated_libraries[lib]['old_version']}, Current version: {updated_libraries[lib]['new_version']}"
            for lib in updated_libraries
        ]
    )
    if SuperviselyDialog(
        f"""The following Python libraries will be restored to the previous versions:
{libraries_list}

Because of this, the <a href='https://pypi.org/project/supervisely/'>Supervisely</a> library will be uninstalled to avoid conflicts and the extension will be disabled.
After the process is complete, 3D Slicer will be restarted.
\nDo you want to proceed?""",
        "confirm",
    ):
        slicer.util.pip_uninstall("supervisely")
        slicer_packages = backup_info.get("before_installation", {})
        installed_packages = [(d.metadata["Name"], d.version) for d in distributions()]

        for package_name, package_version in installed_packages:
            if package_name in slicer_packages:
                if slicer_packages[package_name] != package_version:
                    try:
                        slicer.util.pip_install(f"{package_name}=={slicer_packages[package_name]}")
                    except Exception as e:
                        logging.error(
                            f"Failed to restore {package_name} to {slicer_packages[package_name]} version: {e}"
                        )
        os.remove(RESTORE_LIB_FILE)
        slicer.util.restart()
        button.enabled = False


def get_installed_version(package_name):
    """Return the installed version of a distribution, or None when it is not installed."""
    from importlib.metadata import version

    try:
        return version(package_name)
    except Exception:
        return None


def get_missing_requirements():
    """Return the installed Supervisely release's requirements whose package is not installed.

    Requirements are read from the installed package's own metadata. Environment markers are
    evaluated, so requirements that do not apply to the running interpreter are skipped.
    Packages that are already installed are left as they are, whatever version the SDK declares.
    """
    from importlib.metadata import requires

    from packaging.requirements import Requirement

    missing = []
    for requirement_string in requires("supervisely") or []:
        requirement = Requirement(requirement_string)
        if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
            continue
        if get_installed_version(requirement.name) is None:
            extras = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
            missing.append(f"{requirement.name}{extras}{requirement.specifier}")
    return missing


def install_supervisely():
    """Install the pinned Supervisely release without touching the packages 3D Slicer already has.

    The SDK goes in with --no-deps, then only its requirements that are not installed at all.
    Any package that changed is recorded so that `restore_libraries` can roll it back afterwards.
    """
    import importlib
    import shlex

    # An earlier install may already have changed packages. Keep the versions it recorded as
    # the ones to restore, so this install does not overwrite them with the changed ones.
    previous_before = {}
    if os.path.exists(RESTORE_LIB_FILE):
        with open(RESTORE_LIB_FILE, "r") as f:
            previous_before = json.load(f).get("before_installation", {})

    before_installation = get_installed_libraries_info()
    try:
        if get_installed_version("supervisely") != SUPERVISELY_VERSION:
            slicer.util.pip_install(f"--no-deps supervisely=={SUPERVISELY_VERSION}")
            importlib.invalidate_caches()
        missing = get_missing_requirements()
        if missing:
            slicer.util.pip_install(" ".join(shlex.quote(r) for r in missing))
    finally:
        after_installation = get_installed_libraries_info()
        before_installation = {**before_installation, **previous_before}
        changed = any(
            lib != "supervisely" and lib in before_installation and before_installation[lib] != version
            for lib, version in after_installation.items()
        )
        if previous_before or changed:
            backup_installed_libraries_info(before_installation, after_installation)


def import_supervisely(module):
    from moduleLib import SuperviselyDialog

    installed_version = get_installed_version("supervisely")
    try:
        missing = get_missing_requirements() if installed_version == SUPERVISELY_VERSION else []
    except Exception as e:
        logging.warning(f"Failed to check Supervisely requirements: {e}")
        missing = []

    if installed_version != SUPERVISELY_VERSION or missing:
        # Checked before the import, so an older release that still imports is upgraded too.
        if installed_version is None:
            state = f"{SUPERVISELY_VERSION} to be installed"
        elif installed_version != SUPERVISELY_VERSION:
            state = f"{SUPERVISELY_VERSION} to be installed (installed: {installed_version})"
        else:
            state = f"{SUPERVISELY_VERSION} to have its missing requirements installed"
        SuperviselyDialog(
            f"""
This module requires Python package <a href='https://pypi.org/project/supervisely/'>Supervisely</a> {state}.
It will be installed automatically now. Packages already installed in 3D Slicer are not changed.

3D Slicer will be restarted after installation.
""",
            type="info",
        )
        try:
            install_supervisely()
            slicer.util.restart()
        except Exception:
            SuperviselyDialog(
                """\nFailed to install <a href='https://pypi.org/project/supervisely/'>Supervisely</a> package.
3D Slicer will be restarted now and the installation will be retried.
\nIf the problem persists, please install the package manually or contact us for help.
<a href='https://supervisely.com/slack/'>Supervisely Slack community</a>""",
                "error",
            )
            slicer.util.restart()
        return

    try:
        from supervisely import Api
    except Exception as import_error:
        # The required version and its requirements are installed and it still does not import.
        # Installing it again would change nothing and would bring this dialog back on every
        # launch, so report what actually went wrong instead.
        SuperviselyDialog(
            f"""
The installed <a href='https://pypi.org/project/supervisely/'>Supervisely</a> package ({installed_version}) is the version this module requires, but it could not be imported:

{import_error}

\nPlease resolve this manually or contact us for help.
<a href='https://supervisely.com/slack/'>Supervisely Slack community</a>""",
            "error",
        )
    else:
        module.ready_to_start = True


def clear(logic, local_data: bool = True):
    """Clears the scene and removes local data

    Args:
        logic: BaseLogic instance
        local_data: remove local data or not
    """
    slicer.mrmlScene.Clear()
    if local_data:
        logic.removeLocalData()
    if logic.volume:
        logic.volume.clear()
        logic.volume = None

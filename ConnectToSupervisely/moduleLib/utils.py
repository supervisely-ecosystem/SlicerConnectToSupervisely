import functools
import json
import logging
import os
from importlib.metadata import distributions
from pathlib import Path

import slicer

RESTORE_LIB_FILE = os.path.join(Path.home(), "supervisely_slicer_installed_packages.json")

# The Supervisely SDK release this module installs and is tested against.
SUPERVISELY_VERSION = "6.74.36"

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


def get_dependency_conflicts(target_version):
    """Return the pinned Supervisely release's requirements that clash with what is installed.

    The metadata is read for `target_version`, which is the release this module actually
    installs, and not for whatever happens to be the latest release on PyPI.

    Environment markers are evaluated rather than matched as text, so requirements that do
    not apply to the running interpreter are skipped instead of being reported as conflicts.
    """
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
    from requests import get

    response = get(f"https://pypi.org/pypi/supervisely/{target_version}/json", timeout=60)
    response.raise_for_status()
    requires_dist = response.json()["info"]["requires_dist"] or []

    conflicts = []
    for requirement_string in requires_dist:
        requirement = Requirement(requirement_string)
        if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
            continue
        specifier = str(requirement.specifier)
        installed_version = get_installed_version(requirement.name)
        if (
            installed_version
            and specifier
            and Version(installed_version) not in SpecifierSet(specifier, prereleases=True)
        ):
            conflicts.append((requirement.name, installed_version, specifier))
    return conflicts


def format_dependency_conflicts(conflicts):
    """Render conflicts as the lines shown in the dialog."""
    return "".join(
        f" - [{name}] installed: {installed_version}, "
        f"required: {specifier.replace('<', '&lt;').replace('>', '&gt;')}\n"
        for name, installed_version, specifier in conflicts
    )


def install_supervisely():
    """Install the pinned Supervisely release over conflicting packages, recording what it
    changed so that `restore_libraries` can roll those packages back afterwards."""
    before_installation = get_installed_libraries_info()
    slicer.util.pip_install(f"supervisely=={SUPERVISELY_VERSION}")
    after_installation = get_installed_libraries_info()
    backup_installed_libraries_info(before_installation, after_installation)


def import_supervisely(module):
    from moduleLib import SuperviselyDialog

    try:
        from supervisely import Api
    except Exception as import_error:
        installed_version = get_installed_version("supervisely")

        if installed_version == SUPERVISELY_VERSION:
            # The required version is already installed and still does not import.
            # Installing it again would change nothing and would bring this dialog back on
            # every launch, so report what actually went wrong instead of asking again.
            SuperviselyDialog(
                f"""
The installed <a href='https://pypi.org/project/supervisely/'>Supervisely</a> package ({installed_version}) is the version this module requires, but it could not be imported:

{import_error}

\nPlease resolve this manually or contact us for help.
<a href='https://supervisely.com/slack/'>Supervisely Slack community</a>""",
                "error",
            )
            return

        if installed_version is None:
            state = "to be installed"
        else:
            # Installed, but unusable on this interpreter. Reinstalling the same version is
            # what made this dialog reappear on every launch, so install the pinned one instead.
            state = (
                f"{SUPERVISELY_VERSION} to be installed: "
                f"the installed version ({installed_version}) could not be imported"
            )

        message = format_dependency_conflicts(get_dependency_conflicts(SUPERVISELY_VERSION))

        if message:
            if SuperviselyDialog(
                f"""
This module requires Python package <a href='https://pypi.org/project/supervisely/'>Supervisely</a> {state}.
But it has conflicting dependencies with the installed packages:
\n{message}
Installing will change these packages for the whole 3D Slicer installation. The "Restore Libraries" button in this module rolls that back.

Do you want to install <a href='https://pypi.org/project/supervisely/'>Supervisely</a> package anyway?\n""",
                "confirm",
            ):
                try:
                    install_supervisely()
                    slicer.util.restart()
                except Exception:
                    SuperviselyDialog(
                        """
Failed to install <a href='https://pypi.org/project/supervisely/'>Supervisely</a> package.
\nPlease install it manually and resolve conflicts with the dependencies before opening the module or contact us for help.
<a href='https://supervisely.com/slack/'>Supervisely Slack community</a>

\n3D Slicer will be restarted now automatically.""",
                        "error",
                    )
                    slicer.util.restart()
            else:
                SuperviselyDialog(
                    """
If you need help with the installation, please contact us.
<a href='https://supervisely.com/slack/'>Supervisely Slack community</a>"""
                )
        else:
            SuperviselyDialog(
                f"""
This module requires Python package <a href='https://pypi.org/project/supervisely/'>Supervisely</a> {state}.
It will be installed automatically now.

3D Slicer will be restarted after installation.
""",
                type="info",
            )
            try:
                slicer.util.pip_install(f"supervisely=={SUPERVISELY_VERSION}")
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

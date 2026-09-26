"""UI strings used by more than one widget.

self.tr() files a string under the calling class, so the same text used in two
classes ends up translated twice. Strings shared across classes live here under
a single "Common" context instead.
"""
from PyQt6.QtCore import QCoreApplication


# Each call spells out the literal context and text: pylupdate6 only picks up
# translate() calls it can read statically.
def audio_output() -> str:
    return QCoreApplication.translate("Common", "Audio output")


def system_default() -> str:
    return QCoreApplication.translate("Common", "System default")


def unavailable(name: str) -> str:
    return QCoreApplication.translate("Common", "{0} (unavailable)").format(name)


def label_name() -> str:
    return QCoreApplication.translate("Common", "Label name:")

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from lisa import Node
from lisa.operating_system import Debian, Oracle, Posix, Redhat, Suse, Ubuntu
from lisa.util import UnsupportedDistroException, UnsupportedKernelException

DPDK_STABLE_GIT_REPO = "https://dpdk.org/git/dpdk-stable"


def _check_kernel_version(node: Node, kernel_version: str, supported_os: bool) -> None:
    if isinstance(node.os, Posix):
        kernel = node.os.get_kernel_information()
        if supported_os and kernel.version < kernel_version:
            raise UnsupportedKernelException(
                node.os, f"DPDK requires kernel version {kernel_version} on this OS"
            )


def check_dpdk_support(node: Node) -> None:
    # check requirements according to:
    # https://docs.microsoft.com/en-us/azure/virtual-network/setup-dpdk
    supported = False
    if isinstance(node.os, Debian):
        if isinstance(node.os, Ubuntu):
            supported = node.os.information.version >= "18.4.0"
            _check_kernel_version(node, "4.15.0", supported)
        else:
            supported = node.os.information.version >= "10.0.0"
            _check_kernel_version(node, "4.19.0", supported)
    elif isinstance(node.os, Redhat) and not isinstance(node.os, Oracle):
        supported = node.os.information.version >= "7.5.0"
        _check_kernel_version(node, "3.10.0", supported)
    elif isinstance(node.os, Suse):
        supported = node.os.information.version >= "15.0.0"
        _check_kernel_version(node, "4.12.14", supported)
    else:
        # this OS is not supported
        raise UnsupportedDistroException(
            node.os, "This OS is not supported by the DPDK test suite for Azure."
        )

    if not supported:
        raise UnsupportedDistroException(
            node.os, "This OS version is EOL and is not supported for DPDK on Azure"
        )

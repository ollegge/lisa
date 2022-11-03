# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
import time
from pathlib import Path
from typing import Any, Dict

from assertpy import assert_that

from lisa import (
    Environment,
    Logger,
    Node,
    TestCaseMetadata,
    TestSuite,
    TestSuiteMetadata,
    notifier,
)
from lisa.messages import SubTestMessage, TestStatus, create_test_result_message
from lisa.testsuite import TestResult
from lisa.tools import Dmesg, Free, Ls, Lscpu, QemuImg, Ssh, Wget
from lisa.util import SkippedException
from microsoft.testsuites.mshv.cloud_hypervisor_tool import CloudHypervisor


@TestSuiteMetadata(
    area="mshv",
    category="",
    description="""
    This test suite contains tests that are meant to be run on the
    Microsoft Hypervisor (MSHV) root partition.
    """,
)
class MshvHostTestSuite(TestSuite):
    CONFIG_VARIABLE = "mshv_vm_create_stress_configs"
    DEFAULT_ITERS = 25
    DEFAULT_CPUS_PER_VM = 1
    DEFAULT_MEM_PER_VM_MB = 1024

    HYPERVISOR_FW_NAME = "hypervisor-fw"
    DISK_IMG_NAME = "vm_disk_img.raw"

    def before_suite(self, log: Logger, **kwargs: Any) -> None:
        node = kwargs["node"]
        if not node.tools[Ls].path_exists("/dev/mshv", sudo=True):
            raise SkippedException("This suite is for MSHV root partition only")

        working_path = node.get_working_path()
        node.tools[Wget].get(
            "https://github.com/cloud-hypervisor/rust-hypervisor-firmware/releases/download/0.4.1/hypervisor-fw",  # noqa: E501
            file_path=str(working_path),
            filename=self.HYPERVISOR_FW_NAME,
        )
        node.tools[Wget].get(
            "https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-amd64.img",  # noqa: E501
            file_path=str(working_path),
            filename=f"{self.DISK_IMG_NAME}.img",
        )
        node.tools[QemuImg].convert(
            "qcow2",
            str(working_path / f"{self.DISK_IMG_NAME}.img"),
            "raw",
            str(working_path / self.DISK_IMG_NAME),
        )

    @TestCaseMetadata(
        description="""
        Stress the MSHV virt stack by repeatedly creating and destroying
        multiple VMs in parallel. By default creates VMs with 1 vCPU and
        1 GiB of RAM each. Number of VMs createdis equal to the number of
        CPUs available on the host. By default, the test is repeated 25
        times. All of these can be configured via the variable
        "mshv_vm_create_stress_configs" in the runbook.
        """,
        priority=4,
    )
    def verify_mshv_stress_vm_create(
        self,
        log: Logger,
        node: Node,
        variables: Dict[str, Any],
        environment: Environment,
        log_path: Path,
        result: TestResult,
    ) -> None:
        if self.CONFIG_VARIABLE in variables:
            configs = variables[self.CONFIG_VARIABLE]
        else:
            # fall back to defaults
            configs = [{}]

        # This test can end up creating and a lot of ssh sessions and these kept active
        # at the same time.
        # In Ubuntu, the default limit is easily exceeded. So change the MaxSessions
        # property in sshd_config to a high number that is unlikely to be exceeded.
        node.tools[Ssh].set_max_session()

        failures = 0
        for config in configs:
            times = config.get("iterations", self.DEFAULT_ITERS)
            cpus_per_vm = config.get("cpus_per_vm", self.DEFAULT_CPUS_PER_VM)
            mem_per_vm_mb = config.get("mem_per_vm_mb", self.DEFAULT_MEM_PER_VM_MB)
            test_name = f"mhsv_stress_vm_create_{times}times_{cpus_per_vm}cpu_{mem_per_vm_mb}MB"  # noqa: E501
            try:
                self._mshv_stress_vm_create(
                    times,
                    cpus_per_vm,
                    mem_per_vm_mb,
                    log,
                    node,
                    log_path,
                )
                self._send_subtest_msg(
                    result.id_,
                    environment,
                    test_name,
                    TestStatus.PASSED,
                )
            except Exception as e:
                failures += 1
                self._send_subtest_msg(
                    result.id_, environment, test_name, TestStatus.FAILED, repr(e)
                )
        assert_that(failures).is_equal_to(0)
        return

    def _mshv_stress_vm_create(
        self,
        times: int,
        cpus_per_vm: int,
        mem_per_vm_mb: int,
        log: Logger,
        node: Node,
        log_path: Path,
    ) -> None:
        log.info(
            f"MSHV stress VM create: times={times}, cpus_per_vm={cpus_per_vm}, mem_per_vm_mb={mem_per_vm_mb}"  # noqa: E501
        )
        hypervisor_fw_path = str(node.get_working_path() / self.HYPERVISOR_FW_NAME)
        disk_img_path = str(node.get_working_path() / self.DISK_IMG_NAME)
        cores = node.tools[Lscpu].get_core_count()
        vm_count = int(cores / cpus_per_vm)
        failures = 0
        for test_iter in range(times):
            log.info(f"Test iteration {test_iter + 1} of {times}")
            node.tools[Free].log_memory_stats_mb()
            procs = []
            for i in range(vm_count):
                log.info(f"Starting VM {i}")
                p = node.tools[CloudHypervisor].start_vm_async(
                    kernel=hypervisor_fw_path,
                    cpus=cpus_per_vm,
                    memory_mb=mem_per_vm_mb,
                    disk_path=disk_img_path,
                    disk_readonly=True,
                )
                assert_that(p).described_as(f"Failed to create VM {i}").is_not_none()
                procs.append(p)
                node.tools[Free].log_memory_stats_mb()
                assert_that(p.is_running()).described_as(
                    f"VM {i} failed to start"
                ).is_true()

            # keep the VMs running for a while
            time.sleep(10)

            for i in range(len(procs)):
                p = procs[i]
                if not p.is_running():
                    log.info(f"VM {i} was not running (OOM killed?)")
                    failures += 1
                    continue
                log.info(f"Killing VM {i}")
                p.kill()

            node.tools[Free].log_memory_stats_mb()

        dmesg_str = node.tools[Dmesg].get_output()
        dmesg_path = log_path / f"dmesg_{times}_{cpus_per_vm}_{mem_per_vm_mb}"
        with open(str(dmesg_path), "w") as f:
            f.write(dmesg_str)
        assert_that(failures).is_equal_to(0)

    def _send_subtest_msg(
        self,
        test_id: str,
        environment: Environment,
        test_name: str,
        test_status: TestStatus,
        test_msg: str = "",
    ) -> None:
        subtest_msg = create_test_result_message(
            SubTestMessage,
            test_id,
            environment,
            test_name,
            test_status,
            test_msg,
        )

        notifier.notify(subtest_msg)
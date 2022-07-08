# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
from typing import List, Optional, Type, cast

from lisa.executable import Tool
from lisa.operating_system import Posix
from lisa.tools.hyperv import HyperV
from lisa.tools.powershell import PowerShell


class Mdadm(Tool):
    @property
    def command(self) -> str:
        return "mdadm"

    @property
    def can_install(self) -> bool:
        return True

    def create_raid(
        self,
        disk_list: List[str],
        level: int = 0,
        volume_name: str = "/dev/md0",
        chunk_size: int = 0,
    ) -> None:
        count = len(disk_list)
        disks = " ".join(disk_list)
        cmd = f"--create {volume_name} --level {level} --raid-devices {count} {disks}"
        if chunk_size:
            cmd += " --chunk {chunk_size}"
        self.run(
            cmd,
            sudo=True,
            force_run=True,
            expected_exit_code=0,
            expected_exit_code_failure_message=(
                f"failed to create {volume_name} against disks {disks}"
            ),
        )

    def stop_raid(
        self,
        volume_name: str = "/dev/md0",
    ) -> None:
        self.run(f"--stop {volume_name}", force_run=True, sudo=True)

    @classmethod
    def _windows_tool(cls) -> Optional[Type[Tool]]:
        return WindowsMdadm

    def _install(self) -> bool:
        posix_os: Posix = cast(Posix, self.node.os)
        posix_os.install_packages("mdadm")
        return self._check_exists()


class WindowsMdadm(Mdadm):
    @property
    def command(self) -> str:
        return "powershell"

    def _check_exists(self) -> bool:
        return True

    def create_raid(
        self,
        disk_list: List[str],
        level: int = 0,
        volume_name: str = "Raid0-Disk",
        chunk_size: int = 0,
        pool_name: str = "Raid0-Pool",
    ) -> None:
        powershell = self.node.tools[PowerShell]

        # create pool
        # TODO: add support for higher raid types and chunk sizes
        self._create_pool(pool_name)

        # create new virtual disk
        self.node.tools[HyperV].create_virtual_disk(volume_name, pool_name)

        # set raid disk offline
        raid_disk_id = int(
            powershell.run_cmdlet(
                "(Get-Disk "
                f"| Where-Object {{$_.FriendlyName -eq '{volume_name}'}}).Number",
                force_run=True,
            ).strip()
        )
        powershell.run_cmdlet(
            f"Set-Disk {raid_disk_id} -IsOffline $true", force_run=True
        )

    def stop_raid(
        self, volume_name: str = "Raid0-Disk", pool_name: str = "Raid0-Pool"
    ) -> None:
        # delete virtual disk if it exists
        self.node.tools[HyperV].delete_virtual_disk(volume_name)

        # delete storage pool
        self._delete_pool(pool_name)

    def _exists_pool(self, pool_name: str) -> bool:
        output = self.node.tools[PowerShell].run_cmdlet(
            f"Get-StoragePool -FriendlyName {pool_name}",
            fail_on_error=False,
            force_run=True,
        )
        return output.strip() != ""

    def _delete_pool(self, pool_name: str) -> None:
        if self._exists_pool(pool_name):
            self.node.tools[PowerShell].run_cmdlet(
                f"Remove-StoragePool -FriendlyName {pool_name} -confirm:$false",
                force_run=True,
            )

    def _create_pool(self, pool_name: str) -> None:
        # delete pool if exists
        self._delete_pool(pool_name)

        # create pool
        self.node.tools[PowerShell].run_cmdlet(
            "$disks = Get-PhysicalDisk -CanPool  $true; New-StoragePool "
            "-StorageSubSystemFriendlyName 'Windows Storage*' "
            f"-FriendlyName {pool_name} -PhysicalDisks $disks",
            force_run=True,
        )